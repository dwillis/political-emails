"""Flag senders that are not political committees, using a decision model's majority vote.

For every sender (committee name, or domain when there is none) among emails that
have a party but no FEC committee ID, classifies up to K of its emails with the
party question from eval_party_decision.py and takes a majority vote. A sender is
flagged as not a political committee when the vote says so, unless one of its
voting emails has a candidate-style "Paid for by" disclaimer (e.g. "Keir for AG").
The existing `party` field is left alone; this only produces a review table.

Model calls are cached, so an interrupted run resumes where it stopped.
See PARTY_DECISION_EVAL.md for the evaluation behind these choices.

Example:
    uv run python scripts/classify_senders.py --per-sender 5
"""

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_party_decision import classify, load_cache, load_records, sender_key, vote  # noqa: E402
from utils import DATA_DIR, STATE_DIR, load_jsonl  # noqa: E402

NPC = 'Not a Political Committee'
OUT_DIR = STATE_DIR / 'validation' / 'sender_party'

STATES = ('alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|'
          'georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|'
          'massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|'
          'new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|'
          'oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|'
          'vermont|virginia|washington|west virginia|wisconsin|wyoming|america|[a-z]{2}')
OFFICES = (r'congress|senate|governor|gov|lt\.? governor|ag|attorney general|legislature|assembly|'
           r'delegate|president|mayor|house|treasurer|secretary of state|comptroller|controller|'
           r'council|supervisor|judge|justice|sheriff|district attorney|da|state senate|state house')
CANDIDATE_RE = re.compile(rf'(\b(?:for|4)\s+(?:{OFFICES}|{STATES})\b|\bfriends of\b|'
                          r'\bcommittee to elect\b|\bcampaign\b)', re.IGNORECASE)
PAID_FOR_RE = re.compile(r'^\W*paid for(?: and provided(?: in kind)?)? by\s+', re.IGNORECASE)


def candidate_disclaimer(text):
    """True when the committee named in a "Paid for by" line looks like a candidate's."""
    return bool(CANDIDATE_RE.search(PAID_FOR_RE.sub('', (text or '').strip())))


def index_senders(paths):
    """sender -> list of (path, unique_id), plus per-sender metadata for the review table."""
    emails, meta = defaultdict(list), defaultdict(lambda: defaultdict(Counter))
    for path in paths:
        for rec in load_jsonl(path):
            if rec.get('party') and not rec.get('committee_fec_id'):
                key = sender_key(rec)
                emails[key].append((path, rec['unique_id']))
                for field in ('party', 'party_source', 'name', 'domain'):
                    meta[key][field][rec.get(field) or ''] += 1
    return emails, meta


def top(counter, n=3):
    return '; '.join(f'{k} ({v})' for k, v in counter.most_common(n))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--per-sender', type=int, default=5, help='Emails to classify per sender')
    parser.add_argument('--limit', type=int, help='Only the N largest senders (for testing)')
    parser.add_argument('--out-dir', type=Path, default=OUT_DIR)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--model', default='nimble-v3:9b-mxfp8')
    parser.add_argument('--api-base', default='http://localhost:11434')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.out_dir / 'model_results.jsonl'
    table_path = args.out_dir / 'senders.csv'

    emails, meta = index_senders(sorted(DATA_DIR.glob('*/*/*.jsonl')))
    senders = sorted(emails, key=lambda s: -len(emails[s]))[:args.limit]
    rng = random.Random(args.seed)
    chosen = {s: rng.sample(emails[s], min(args.per_sender, len(emails[s]))) for s in senders}

    cache = load_cache(cache_path, args.model)
    all_rows = [(path, uid) for picks in chosen.values() for path, uid in picks]
    records = load_records(all_rows)
    pending = [uid for _, uid in all_rows if uid not in cache]
    print(f'{len(senders)} senders, {sum(len(emails[s]) for s in senders)} emails; '
          f'{len(all_rows)} voting emails, {len(all_rows) - len(pending)} cached, '
          f'{len(pending)} to classify with {args.model}', file=sys.stderr)

    with cache_path.open('a') as out, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, row in enumerate(pool.map(lambda uid: classify(records[uid], args), pending), 1):
            if 'model' in row:
                cache[row['unique_id']] = row
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
                out.flush()
            else:
                print(f"  error on {row['unique_id']}: {row.get('error')}", file=sys.stderr)
            if i % 250 == 0:
                print(f'  {i}/{len(pending)}', file=sys.stderr)

    flagged = flagged_emails = 0
    with table_path.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['sender', 'emails', 'party', 'party_source', 'names', 'domains',
                         'classified', 'votes', 'vote', 'candidate_disclaimer',
                         'not_political_committee', 'model'])
        for s in senders:
            voters = [cache[uid] for _, uid in chosen[s] if uid in cache]
            result = vote(voters) if voters else ''
            guard = any(candidate_disclaimer(records[uid].get('disclaimer_text')) for _, uid in chosen[s])
            npc = result == NPC and not guard
            flagged += npc
            flagged_emails += len(emails[s]) if npc else 0
            writer.writerow([s, len(emails[s]), top(meta[s]['party']), top(meta[s]['party_source']),
                             top(meta[s]['name']), top(meta[s]['domain']), len(voters),
                             json.dumps(dict(Counter(v['model'] for v in voters))), result,
                             guard, npc, args.model])
    print(f'Wrote {table_path}: {flagged} of {len(senders)} senders ({flagged_emails} emails) '
          f'flagged as not political committees', file=sys.stderr)


if __name__ == '__main__':
    main()
