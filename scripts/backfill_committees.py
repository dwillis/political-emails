"""One-time backfill of the `committee` attribute into the daily JSONL archive.

Reads committee assignments from the qwen-enriched export
(emails_updated_qwen35-4b.json, a ~4 GB pretty-printed JSON array) and joins
them onto the archive records by (email, subject, date). Adds a `committee` key
to every archive record: the matched name, or None when unknown/unmatched.

Idempotent: a non-null committee already on a record is never overwritten, so
re-runs (and records already enriched by Ollama) are preserved.

Run with ijson available for streaming the large file:

    uv run --with ijson python scripts/backfill_committees.py [--dry-run]
        [--limit-days N] [--qwen-path PATH]
"""

import argparse
import sys
from pathlib import Path

import ijson

from committee_utils import build_committee_map, iter_day_files, record_key
from utils import load_jsonl, save_jsonl

DEFAULT_QWEN_PATH = Path(__file__).resolve().parent.parent / "emails_updated_qwen35-4b.json"


def load_committee_map(qwen_path):
    """Stream the qwen JSON array and build a {join_key: committee} map."""
    print(f"Streaming {qwen_path} (ijson backend: {ijson.backend})...")

    def records():
        with open(qwen_path, "rb") as f:
            count = 0
            for record in ijson.items(f, "item"):
                count += 1
                if count % 50000 == 0:
                    print(f"  parsed {count:,} records...")
                yield record

    mapping, stats = build_committee_map(records())
    print(
        f"Parsed {stats['parsed']:,} records -> {stats['distinct_keys']:,} distinct keys "
        f"({stats['mapped_real']:,} with a committee, {stats['mapped_null']:,} null, "
        f"{stats['conflicts']:,} conflicts blanked)."
    )
    return mapping


def apply_to_day(path, committee_map):
    """Add committee to each record in a day file. Returns per-file counts."""
    records = load_jsonl(path)
    counts = {"matched_real": 0, "matched_null": 0, "unmatched": 0, "already_set": 0}
    changed = False

    for rec in records:
        if rec.get("committee") is not None:
            counts["already_set"] += 1
            continue
        key = record_key(rec)
        if key in committee_map:
            value = committee_map[key]
            counts["matched_real" if value is not None else "matched_null"] += 1
        else:
            value = None
            counts["unmatched"] += 1
        # Set the key on every record for uniform schema (None when unknown).
        rec["committee"] = value
        changed = True

    return records, counts, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen-path", type=Path, default=DEFAULT_QWEN_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Don't write files")
    parser.add_argument("--limit-days", type=int, default=None, help="Process only the first N day files (testing)")
    args = parser.parse_args()

    if not args.qwen_path.exists():
        sys.exit(f"qwen file not found: {args.qwen_path}")

    committee_map = load_committee_map(args.qwen_path)

    day_files = iter_day_files()
    if args.limit_days:
        day_files = day_files[: args.limit_days]

    totals = {"matched_real": 0, "matched_null": 0, "unmatched": 0, "already_set": 0}
    files_rewritten = 0

    for i, path in enumerate(day_files, 1):
        records, counts, changed = apply_to_day(path, committee_map)
        for k in totals:
            totals[k] += counts[k]
        if changed and not args.dry_run:
            save_jsonl(path, records)
            files_rewritten += 1
        elif changed:
            files_rewritten += 1  # would rewrite
        if i % 500 == 0:
            print(f"  processed {i:,}/{len(day_files):,} day files...")

    action = "would rewrite" if args.dry_run else "rewrote"
    print(
        f"\nDone. {action} {files_rewritten:,} of {len(day_files):,} day files.\n"
        f"  matched with committee: {totals['matched_real']:,}\n"
        f"  matched but unknown:    {totals['matched_null']:,}\n"
        f"  unmatched (null):       {totals['unmatched']:,}\n"
        f"  already set (skipped):  {totals['already_set']:,}"
    )


if __name__ == "__main__":
    main()
