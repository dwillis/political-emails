"""Compare a decision model's party label against the existing `party` field.

Samples emails that have a party but no FEC committee ID (so the party came from
domain maps, candidate matches, name keywords, overrides, etc.), asks the model
to choose Democrat / Republican / Other Party / Not a Political Committee via
Ollama's /v1/systemone endpoint, and writes per-email results plus a summary.

With --per-sender K, each sampled email's sender (committee name, or domain when
there is none) is also labeled by majority vote over up to K of that sender's
eligible emails, so one atypical email doesn't decide the label.

Examples:
    uv run python scripts/eval_party_decision.py --sample 100 -o party_eval.jsonl
    uv run python scripts/eval_party_decision.py --strata domain-map=150,legacy=100,other=100 \\
        --per-sender 5 --cache party_cache.jsonl -o party_eval.jsonl
"""

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_scripture import MAX_CHARS, request_json  # noqa: E402
from utils import DATA_DIR, load_jsonl  # noqa: E402

CHOICES = ['Democrat', 'Republican', 'Other Party', 'Not a Political Committee']
EXISTING = {'D': 'Democrat', 'R': 'Republican', 'I': 'Other Party', 'G': 'Other Party'}

QUESTION = {
    'type': 'choice',
    'instructions': ('Which party, if any, is the political committee, campaign, or '
                     'organization that sent this email affiliated with?'),
    'criteria': {
        'Democrat': ('Sent by a Democratic candidate, party committee, or a PAC or group '
                     'that works to elect Democrats.'),
        'Republican': ('Sent by a Republican candidate, party committee, or a PAC or group '
                       'that works to elect Republicans.'),
        'Other Party': ('Sent by an independent candidate, a third party (Green, Libertarian, '
                        'etc.), or a political committee not aligned with either major party.'),
        'Not a Political Committee': ('Sent by a business, media outlet, newsletter, '
                                      'advocacy nonprofit, or ministry that is not a candidate, '
                                      'party, or political committee, even if its content is '
                                      'partisan.'),
    },
}


def sender_key(rec):
    """Committee name when known, else the sending domain."""
    name = (rec.get('committee_canonical') or rec.get('committee') or '').strip().lower()
    return name or f"domain:{(rec.get('domain') or '').lower()}"


def state_text(rec):
    header = '\n'.join(f'{label}: {rec.get(key) or ""}' for label, key in (
        ('From name', 'name'), ('From address', 'email'), ('Subject', 'subject'),
        ('Paid-for disclaimer', 'disclaimer_text')))
    body = (rec.get('clean_body') or rec.get('body') or '').strip()
    return f'{header}\n\nBody:\n{body}'[:MAX_CHARS]


def classify(rec, args):
    row = {'unique_id': rec['unique_id'], 'model_name': args.model,
           'date': rec.get('date'), 'name': rec.get('name'),
           'email': rec.get('email'), 'subject': rec.get('subject'),
           'committee': rec.get('committee'), 'sender': sender_key(rec), 'party': rec['party'],
           'party_source': rec.get('party_source'), 'existing': EXISTING.get(rec['party'])}
    payload = {'model': args.model, 'state': state_text(rec),
               'questions': {'party': QUESTION}}
    try:
        answer = request_json(args.api_base, '/v1/systemone', payload, args.timeout)['answers']['party']
    except (KeyError, TypeError, ValueError, OSError, URLError) as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
        return row
    row['model'] = answer['choice']
    row['probabilities'] = {k: round(v, 4) for k, v in answer['probabilities'].items()}
    row['agree'] = row['model'] == row['existing']
    return row


def index_eligible(paths):
    """Lightweight index of every eligible email: (path, unique_id, sender, party_source)."""
    index = []
    for path in paths:
        for rec in load_jsonl(path):
            if rec.get('party') and not rec.get('committee_fec_id'):
                index.append((path, rec['unique_id'], sender_key(rec), rec.get('party_source')))
    return index


def load_records(index_rows):
    wanted = defaultdict(set)
    for path, uid, *_ in index_rows:
        wanted[path].add(uid)
    return {rec['unique_id']: rec for path, uids in wanted.items()
            for rec in load_jsonl(path) if rec['unique_id'] in uids}


def parse_strata(spec):
    """'domain-map=150,legacy=100,other=100' -> {'domain-map': 150, ...}"""
    return {k: int(v) for k, v in (part.split('=') for part in spec.split(','))}


def draw_sample(index, args, rng):
    if not args.strata:
        return rng.sample(index, min(args.sample, len(index)))
    strata = parse_strata(args.strata)
    groups = defaultdict(list)
    for row in index:
        groups[row[3] if row[3] in strata else 'other'].append(row)
    picked = []
    for name, n in strata.items():
        picked += rng.sample(groups[name], min(n, len(groups[name])))
    return picked


def voters_for(sample, index, k, rng):
    """Up to k eligible emails per sampled sender, always including the sampled ones."""
    by_sender = defaultdict(list)
    for row in index:
        by_sender[row[2]].append(row)
    chosen = {}
    for sender in {row[2] for row in sample}:
        seeds = [r for r in sample if r[2] == sender]
        rest = [r for r in by_sender[sender] if r not in seeds]
        extra = rng.sample(rest, max(0, min(k - len(seeds), len(rest))))
        for row in seeds + extra:
            chosen[row[1]] = row
    return list(chosen.values())


def vote(rows):
    """Plurality of model labels; ties broken by summed probability."""
    counts = Counter(r['model'] for r in rows)
    probs = Counter()
    for r in rows:
        probs.update(r['probabilities'])
    return max(counts, key=lambda c: (counts[c], probs[c]))


def load_cache(path, model):
    """Cached results for this model only, so one cache file can hold several models."""
    if not path or not path.exists():
        return {}
    return {row['unique_id']: row for row in load_jsonl(path)
            if 'model' in row and row.get('model_name') == model}


def summarize(rows):
    scored = [r for r in rows if 'model' in r]
    if not scored:
        return 'Nothing scored'
    lines = []
    for field, label in (('model', 'Per-email model'), ('sender_vote', 'Sender vote')):
        if not any(field in r for r in scored):
            continue
        agree = sum(r.get(field) == r['existing'] for r in scored)
        lines += [f'{label}: agreement with existing {agree}/{len(scored)} ({agree / len(scored):.0%})',
                  '  existing -> model counts:']
        for (existing, model), n in sorted(Counter((r['existing'], str(r.get(field))) for r in scored).items()):
            lines.append(f'    {existing:12} -> {model:26} {n}')
        lines.append('  agreement by party_source:')
        for source, n in Counter(r['party_source'] for r in scored).most_common():
            a = sum(r.get(field) == r['existing'] for r in scored if r['party_source'] == source)
            lines.append(f'    {source:20} {a}/{n}')
        lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('files', nargs='*', type=Path,
                        help='Daily JSONL files (default: everything under data/)')
    parser.add_argument('-o', '--output', type=Path, required=True,
                        help='Per-email results for the sampled emails (JSONL)')
    parser.add_argument('--sample', type=int, default=100, help='Simple random sample size')
    parser.add_argument('--strata', help="Stratified sample by party_source instead of --sample, "
                        "e.g. 'domain-map=150,legacy=100,other=100'")
    parser.add_argument('--per-sender', type=int, default=0,
                        help='Label each sender by majority vote over up to K of its emails')
    parser.add_argument('--cache', type=Path,
                        help='JSONL of model results to reuse and append to (skips repeat calls)')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--model', default='nimble-v3:9b-mxfp8')
    parser.add_argument('--api-base', default='http://localhost:11434')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    files = args.files or sorted(DATA_DIR.glob('*/*/*.jsonl'))
    index = index_eligible(files)
    sample = draw_sample(index, args, rng)
    to_run = voters_for(sample, index, args.per_sender, rng) if args.per_sender else sample

    cache = load_cache(args.cache, args.model)
    pending = [row for row in to_run if row[1] not in cache]
    print(f'Sampled {len(sample)} of {len(index)} eligible emails '
          f'({len({r[2] for r in sample})} senders); {len(to_run)} to classify, '
          f'{len(to_run) - len(pending)} cached; model {args.model}', file=sys.stderr)

    records = load_records(pending)
    cache_out = args.cache.open('a') if args.cache else None
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for i, row in enumerate(pool.map(lambda r: classify(records[r[1]], args), pending), 1):
                cache[row['unique_id']] = row
                if cache_out and 'model' in row:
                    cache_out.write(json.dumps(row, ensure_ascii=False) + '\n')
                    cache_out.flush()
                if i % 50 == 0:
                    print(f'  {i}/{len(pending)}', file=sys.stderr)
    finally:
        if cache_out:
            cache_out.close()

    rows = [dict(cache[r[1]]) for r in sample]
    if args.per_sender:
        by_sender = defaultdict(list)
        for r in to_run:
            if 'model' in cache.get(r[1], {}):
                by_sender[r[2]].append(cache[r[1]])
        for row in rows:
            voters = by_sender.get(row['sender'], [])
            if voters:
                row['sender_vote'] = vote(voters)
                row['sender_votes'] = dict(Counter(v['model'] for v in voters))
    with args.output.open('w') as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(summarize(rows))


if __name__ == '__main__':
    main()
