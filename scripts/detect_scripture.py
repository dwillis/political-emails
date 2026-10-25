"""Flag emails whose body references a Bible scripture, using the nimble-v3 decision model.

Sends each email body to Ollama's /v1/systemone endpoint as a `noul` (Boolean)
question and records the model's probability that the answer is true.

Examples:
    uv run python scripts/detect_scripture.py data/2025/03/2025-03-10.jsonl
    uv run python scripts/detect_scripture.py data/2025/03/*.jsonl --sample 200 -o scripture.jsonl
    uv run python scripts/detect_scripture.py data/2025/*/*.jsonl --match 'psalm|proverbs|\\d+:\\d+' --only-hits
"""

import argparse
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import load_jsonl  # noqa: E402

# Roughly 4 characters per token; leaves headroom in the 32K context for the
# formatted question.
MAX_CHARS = 100_000

QUESTION = {
    'type': 'noul',
    'instructions': 'Does the email body reference a specific Bible scripture?',
    'criteria': {
        'true': ('The body quotes or cites a specific Bible passage: a book/chapter/verse '
                 'citation (e.g. "John 3:16", "Psalm 23"), a named book of the Bible used '
                 'as a source, or a recognizable direct quotation of a Bible verse.'),
        'false': ('The body does not reference a specific Bible passage. General religious '
                  'language such as mentions of God, faith, prayer, blessings, church, or '
                  '"God bless America" does not count on its own.'),
    },
}


def request_json(base, route, payload, timeout):
    request = Request(base.rstrip('/') + route, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def email_text(rec):
    return (rec.get('clean_body') or rec.get('body') or '').strip()


def classify(rec, args):
    text = email_text(rec)
    row = {'unique_id': rec.get('unique_id'), 'date': rec.get('date'),
           'committee': rec.get('committee'), 'subject': rec.get('subject'),
           'truncated': len(text) > MAX_CHARS, 'scripture_prob': None,
           'references_scripture': None}
    if not text:
        row['error'] = 'empty body'
        return row
    payload = {'model': args.model, 'state': text[:MAX_CHARS],
               'questions': {'scripture': QUESTION}}
    try:
        response = request_json(args.api_base, '/v1/systemone', payload, args.timeout)
        prob = response['answers']['scripture']['noul']
    except (KeyError, TypeError, ValueError, OSError, URLError) as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
        return row
    row['scripture_prob'] = round(prob, 4)
    row['references_scripture'] = prob >= args.threshold
    return row


def load_emails(paths, match):
    pattern = re.compile(match, re.IGNORECASE) if match else None
    for path in paths:
        for rec in load_jsonl(path):
            if pattern is None or pattern.search(email_text(rec)):
                yield rec


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('files', nargs='+', type=Path, help='Daily JSONL files under data/')
    parser.add_argument('-o', '--output', type=Path, help='Write results as JSONL (default: stdout)')
    parser.add_argument('--model', default='nimble-v3:9b-mxfp8')
    parser.add_argument('--api-base', default='http://localhost:11434')
    parser.add_argument('--threshold', type=float, default=0.5,
                        help='Probability at or above which an email is flagged (default: 0.5)')
    parser.add_argument('--match', help='Only classify emails whose body matches this regex (case-insensitive)')
    parser.add_argument('--sample', type=int, help='Classify a random sample of N emails')
    parser.add_argument('--limit', type=int, help='Classify at most the first N emails')
    parser.add_argument('--seed', type=int, default=42, help='Random seed for --sample')
    parser.add_argument('--only-hits', action='store_true', help='Only output flagged emails')
    parser.add_argument('--workers', type=int, default=2, help='Concurrent Ollama requests (default: 2)')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()

    emails = list(load_emails(args.files, args.match))
    if args.sample and args.sample < len(emails):
        emails = random.Random(args.seed).sample(emails, args.sample)
    if args.limit:
        emails = emails[:args.limit]
    print(f'Classifying {len(emails)} emails with {args.model}', file=sys.stderr)

    out = args.output.open('w') if args.output else sys.stdout
    hits = errors = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for i, row in enumerate(pool.map(lambda r: classify(r, args), emails), 1):
                hits += bool(row['references_scripture'])
                errors += 'error' in row
                if not args.only_hits or row['references_scripture']:
                    out.write(json.dumps(row, ensure_ascii=False) + '\n')
                    out.flush()
                if i % 50 == 0:
                    print(f'  {i}/{len(emails)} done, {hits} flagged', file=sys.stderr)
    finally:
        if args.output:
            out.close()
    print(f'Done: {hits} of {len(emails)} flagged at threshold {args.threshold}'
          f'{f", {errors} errors" if errors else ""}', file=sys.stderr)


if __name__ == '__main__':
    main()
