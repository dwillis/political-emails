"""Apply reviewed committee decisions back into the archive.

Reads the CSV exported by the review page (build_review_site.py) and updates
matching records. "disclaimer" and "correct" decisions set the committee and
mark committee_source="human" (the most authoritative provenance). "keep" and
"skip" change nothing.

    uv run python scripts/apply_corrections.py committee_decisions.csv --dry-run
    uv run python scripts/apply_corrections.py committee_decisions.csv
"""

import argparse
import csv
from collections import defaultdict

from committee_utils import normalize_committee
from utils import day_path, load_jsonl, save_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decisions", help="CSV exported from the review page")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.decisions, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Group actionable decisions by day file (from the record's date).
    by_day = defaultdict(dict)  # day_path -> {unique_id: corrected_committee}
    counts = {"keep": 0, "skip": 0, "disclaimer": 0, "correct": 0}
    for r in rows:
        choice = r.get("choice")
        counts[choice] = counts.get(choice, 0) + 1
        if choice not in ("disclaimer", "correct"):
            continue
        corrected = normalize_committee(r.get("corrected_committee"))
        if not corrected:
            continue
        date = r.get("date") or ""
        try:
            y, m, d = date[:10].split("-")
            path = day_path(int(y), int(m), int(d))
        except (ValueError, IndexError):
            print(f"  [skip] unparseable date for {r.get('unique_id')}: {date!r}")
            continue
        by_day[path][r["unique_id"]] = corrected

    updated = 0
    not_found = 0
    for path, fixes in by_day.items():
        records = load_jsonl(path)
        changed = False
        seen = set()
        for rec in records:
            uid = rec.get("unique_id")
            if uid in fixes:
                seen.add(uid)
                if rec.get("committee") != fixes[uid] or rec.get("committee_source") != "human":
                    rec["committee"] = fixes[uid]
                    rec["committee_source"] = "human"
                    updated += 1
                    changed = True
        not_found += len(set(fixes) - seen)
        if changed and not args.dry_run:
            save_jsonl(path, records)

    action = "would update" if args.dry_run else "updated"
    print(f"decisions: {counts}")
    print(f"{action} {updated} records (committee_source=human); {not_found} unique_ids not found")


if __name__ == "__main__":
    main()
