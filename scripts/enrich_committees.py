"""Monthly committee enrichment using a local model via Ollama.

Scans daily JSONL files for records without a committee (null/missing) and asks
a local Ollama model to identify the sending committee, writing the result back
to the archive. Intended to be run manually each month against recently
collected emails -- it is deliberately NOT wired into GitHub Actions.

    uv run python scripts/enrich_committees.py --month 2026-02
    uv run python scripts/enrich_committees.py --since 2026-02-01 --until 2026-02-28

Requires a running Ollama (https://ollama.com) with the target model pulled.

Resumability: each day file is rewritten as soon as it finishes, so a crash
loses at most the in-progress day, and re-running skips records already filled.
Note: because unknown results are stored as null (same as "never processed"),
re-runs will retry previously-unknown records -- acceptable for manual runs.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from committee_utils import needs_committee, normalize_committee
from utils import DATA_DIR, load_jsonl, save_jsonl

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "committee_prompt.txt"
DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


# --- Ollama request (isolated so swapping endpoint/params is a one-fn change) ---

def query_ollama(prompt, model, ollama_url):
    """Send a single prompt to Ollama's /api/generate and return the raw text."""
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get("response", "")


def parse_model_output(text):
    """Extract a committee name from raw model output, or None if unknown."""
    if not text:
        return None
    # Strip code fences and take the first non-empty line.
    lines = [ln.strip().strip("`").strip('"').strip() for ln in text.splitlines()]
    first = next((ln for ln in lines if ln), "")
    return normalize_committee(first)


# --- Day-file selection ---

def day_files_for_range(start, end):
    """Yield daily JSONL paths whose YYYY-MM-DD stem falls within [start, end]."""
    for path in sorted(DATA_DIR.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.jsonl")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start <= day <= end:
            yield path


def resolve_range(args):
    """Return (start_date, end_date) from --month or --since/--until."""
    if args.month:
        year, month = (int(x) for x in args.month.split("-"))
        start = date(year, month, 1)
        first_next = date(year + (month == 12), (month % 12) + 1, 1)
        return start, first_next - timedelta(days=1)
    if args.since and args.until:
        return date.fromisoformat(args.since), date.fromisoformat(args.until)
    # Default: previous calendar month.
    first_this = date.today().replace(day=1)
    end = first_this - timedelta(days=1)
    return end.replace(day=1), end


def build_prompt(template, record, body_chars):
    """Fill the prompt template from a record, truncating the body."""
    body = (record.get("clean_body") or record.get("body") or "")[:body_chars]
    return template.format(
        name=record.get("name", ""),
        email=record.get("email", ""),
        subject=record.get("subject", ""),
        disclaimer_text=record.get("disclaimer_text", ""),
        clean_body=(record.get("clean_body") or "")[:body_chars],
        body=body,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_argument_group("date range (default: previous month)")
    group.add_argument("--month", help="YYYY-MM to process")
    group.add_argument("--since", help="YYYY-MM-DD start (inclusive)")
    group.add_argument("--until", help="YYYY-MM-DD end (inclusive)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--body-chars", type=int, default=4000, help="Max body chars sent to the model")
    parser.add_argument("--limit", type=int, default=None, help="Max model calls (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Query but don't write files")
    args = parser.parse_args()

    template = args.prompt_file.read_text()
    start, end = resolve_range(args)
    print(f"Enriching committees for {start} .. {end} using model '{args.model}'")

    calls = 0
    filled = 0
    unknown = 0
    errors = 0

    for path in day_files_for_range(start, end):
        records = load_jsonl(path)
        pending = [r for r in records if needs_committee(r)]
        if not pending:
            continue

        day_changed = False
        for rec in pending:
            if args.limit is not None and calls >= args.limit:
                break
            prompt = build_prompt(template, rec, args.body_chars)
            try:
                raw = query_ollama(prompt, args.model, args.ollama_url)
            except urllib.error.URLError as e:
                sys.exit(f"Ollama request failed ({args.ollama_url}): {e}. Is Ollama running?")
            except Exception as e:  # noqa: BLE001 - log and continue on per-record errors
                print(f"  [error] {path.stem} {rec.get('email')}: {e}")
                errors += 1
                calls += 1
                continue

            calls += 1
            committee = parse_model_output(raw)
            if committee is not None:
                rec["committee"] = committee
                filled += 1
                day_changed = True
            else:
                unknown += 1

        if day_changed and not args.dry_run:
            save_jsonl(path, records)
        print(f"  {path.stem}: {len(pending)} pending, {calls} calls so far")

        if args.limit is not None and calls >= args.limit:
            print(f"Reached --limit {args.limit}; stopping.")
            break

    action = "would fill" if args.dry_run else "filled"
    print(
        f"\nDone. {calls:,} model calls: {action} {filled:,} committees, "
        f"{unknown:,} returned unknown, {errors:,} errors."
    )


if __name__ == "__main__":
    main()
