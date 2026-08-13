"""Monthly committee enrichment using the DSPy IdentifyCommitteeModule.

Scans daily JSONL files for records without a committee (null/missing) and runs
each one through scripts/identify_committee.py, which first parses the
"Paid for by ..." disclaimer deterministically and falls back to a local LLM
(via DSPy + Ollama) only when that fails. Results are written back to the
archive. Intended to be run manually each month -- deliberately NOT in GitHub
Actions.

    uv run --group enrich python scripts/enrich_committees.py --month 2026-02
    uv run --group enrich python scripts/enrich_committees.py --since 2026-02-01 --until 2026-02-28

Requires DSPy and a running Ollama (https://ollama.com) with the target model
pulled (e.g. `ollama pull qwen3:4b`).

Resumability: each day file is rewritten as soon as it finishes, so a crash
loses at most the in-progress day, and re-running skips records already filled.
Note: unknown results are stored as null (same as "never processed"), so
re-running a month retries any records still unresolved.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from committee_utils import needs_committee, normalize_committee
from utils import DATA_DIR, load_jsonl, save_jsonl

DEFAULT_MODEL = "qwen3.5:4b-mlx"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def configure_dspy(model, ollama_url):
    """Point DSPy at a local Ollama model for the module's LLM fallback."""
    import dspy

    lm = dspy.LM(f"ollama_chat/{model}", api_base=ollama_url)
    dspy.configure(lm=lm)


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


def is_connection_error(exc):
    """True if the exception looks like Ollama being unreachable."""
    text = str(exc).lower()
    return any(s in text for s in ("connection", "connect", "refused", "max retries"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_argument_group("date range (default: previous month)")
    group.add_argument("--month", help="YYYY-MM to process")
    group.add_argument("--since", help="YYYY-MM-DD start (inclusive)")
    group.add_argument("--until", help="YYYY-MM-DD end (inclusive)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag for the LLM fallback")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--limit", type=int, default=None, help="Max records to process (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Identify but don't write files")
    args = parser.parse_args()

    configure_dspy(args.model, args.ollama_url)
    # Import after imports so a missing dspy fails inside configure_dspy above.
    from identify_committee import IdentifyCommitteeModule

    module = IdentifyCommitteeModule()
    start, end = resolve_range(args)
    print(f"Enriching committees for {start} .. {end} (LLM fallback: Ollama '{args.model}')")

    processed = 0
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
            if args.limit is not None and processed >= args.limit:
                break
            try:
                prediction = module(email_body=rec.get("body") or "")
            except Exception as exc:  # noqa: BLE001
                if processed == 0 or is_connection_error(exc):
                    sys.exit(f"LLM call failed ({args.ollama_url}, model '{args.model}'): {exc}\n"
                             "Is Ollama running and the model pulled?")
                print(f"  [error] {path.stem} {rec.get('email')}: {exc}")
                errors += 1
                processed += 1
                continue

            processed += 1
            committee = normalize_committee(prediction.committee)
            if committee is not None:
                rec["committee"] = committee
                filled += 1
                day_changed = True
            else:
                unknown += 1

        if day_changed and not args.dry_run:
            save_jsonl(path, records)
        print(f"  {path.stem}: {len(pending)} pending, {processed} processed so far")

        if args.limit is not None and processed >= args.limit:
            print(f"Reached --limit {args.limit}; stopping.")
            break

    action = "would fill" if args.dry_run else "filled"
    print(
        f"\nDone. Processed {processed:,} records: {action} {filled:,} committees, "
        f"{unknown:,} unresolved (null), {errors:,} errors."
    )


if __name__ == "__main__":
    main()
