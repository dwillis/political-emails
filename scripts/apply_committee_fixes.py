"""One-time sweep that adds committee_source and improves committee coverage.

For every archive record, in this order (order matters):
  1. NULL garbage committee values (extended normalize_committee).
  2. RECOVER: if committee is null, extract it from the "Paid for by" disclaimer
     (fixed committee_extract) -> committee_source="disclaimer".
  3. INFER source for any remaining labeled record: "disclaimer" if the
     deterministic extract matches the stored label, else "backfill".
Then re-orders keys so committee/committee_source sit last (stable diffs).

Idempotent: a second run makes no changes. Rewrites the data/ tree ONCE; commit
data separately from code.

    uv run python scripts/apply_committee_fixes.py --month 2024-11 --dry-run
    uv run python scripts/apply_committee_fixes.py --dry-run     # full preview
    uv run python scripts/apply_committee_fixes.py               # apply
"""

import argparse
from collections import Counter

from committee_extract import extract_committee, looks_confident
from committee_utils import iter_day_files, norm_label, normalize_committee
from utils import DATA_DIR, load_jsonl, save_jsonl


def fix_record(rec):
    """Mutate one record in place. Returns a set of change tags (empty = no-op)."""
    tags = set()
    had_source_key = "committee_source" in rec
    committee = rec.get("committee")
    source = rec.get("committee_source")
    body = rec.get("body") or ""

    # 1. Null / renormalize garbage.
    if committee is not None:
        cleaned = normalize_committee(committee)
        if cleaned is None:
            committee, source = None, None
            tags.add("nulled")
        elif cleaned != committee:
            committee = cleaned
            tags.add("renormalized")

    # 2. Recover a missing committee from the disclaimer.
    if committee is None:
        name = extract_committee(body)
        if looks_confident(name):
            recovered = normalize_committee(name)
            if recovered:
                committee, source = recovered, "disclaimer"
                tags.add("recovered")

    # 3. Infer provenance for any labeled record still lacking a source.
    if committee is not None and source is None:
        det = extract_committee(body)
        if det and looks_confident(det) and norm_label(det) == norm_label(committee):
            source = "disclaimer"
            tags.add("inferred_disclaimer")
        else:
            source = "backfill"
            tags.add("inferred_backfill")

    # 4. Re-order: committee then committee_source at the record's end.
    rec.pop("committee", None)
    rec.pop("committee_source", None)
    rec["committee"] = committee
    rec["committee_source"] = source

    if not had_source_key:
        tags.add("source_key_added")
    return tags


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--month", help="Limit to YYYY-MM (smoke test)")
    parser.add_argument("--limit-days", type=int, default=None)
    args = parser.parse_args()

    day_files = iter_day_files()
    if args.month:
        y, m = args.month.split("-")
        day_files = [p for p in day_files if p.stem.startswith(f"{y}-{m}")]
    if args.limit_days:
        day_files = day_files[: args.limit_days]

    counts = Counter()
    files_changed = 0
    examples = []

    for i, path in enumerate(day_files, 1):
        records = load_jsonl(path)
        changed = False
        for rec in records:
            before = rec.get("committee")
            tags = fix_record(rec)
            # a tag other than the one-time key addition means real content change
            content_tags = tags - {"source_key_added"}
            if content_tags:
                changed = True
                for t in content_tags:
                    counts[t] += 1
                if "nulled" in content_tags and len(examples) < 15 and before:
                    examples.append(before[:70])
            if "source_key_added" in tags:
                counts["source_key_added"] += 1
                changed = True
        if changed and not args.dry_run:
            save_jsonl(path, records)
        if changed:
            files_changed += 1
        if i % 500 == 0:
            print(f"  {i:,}/{len(day_files):,} files...")

    action = "would change" if args.dry_run else "changed"
    print(f"\n{action} {files_changed:,} of {len(day_files):,} files")
    for tag in ("source_key_added", "recovered", "nulled", "renormalized",
                "inferred_disclaimer", "inferred_backfill"):
        print(f"  {tag:20} {counts[tag]:,}")
    if examples:
        print("\n  example nulled values:")
        for ex in examples:
            print(f"    - {ex!r}")


if __name__ == "__main__":
    main()
