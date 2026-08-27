"""Populate `committee_fec_id` on archive records from the FEC committee master.

Each record's `committee` name is matched against the FEC bulk committee master
(via fec_match) and the canonical FEC committee ID is stored as
`committee_fec_id` (or None when there is no committee or no confident match).

Only EXACT normalized-name matches are stored -- fuzzy matches are a review hint
elsewhere and are never authoritative enough to merge distinct committees under
one ID. Storing the ID lets downstream aggregation (e.g. the sender-mention
tracker) group name variants ("...JFC, Inc." vs "...JFC Inc") that resolve to the
same real federal committee, without build_site needing the FEC data.

Idempotent: the ID is recomputed from `committee` each run, so re-running keeps
records in sync as committee names or the FEC cache change. Records whose
committee is not a federal committee (state/local) simply get None.

    uv run python scripts/backfill_fec_ids.py --dry-run       # report only
    uv run python scripts/backfill_fec_ids.py                 # write changes
    uv run python scripts/backfill_fec_ids.py --month 2026-08 # smoke test
"""

import argparse
from collections import Counter

from committee_utils import iter_day_files
from utils import load_jsonl, save_jsonl


def resolve_fec_id(committee, name_index, buckets, cache):
    """Return the exact-match FEC ID for a committee name, or None.

    Results are memoized on the normalized name so each distinct committee is
    matched once across the whole archive.
    """
    from fec_match import match_name

    if not committee or not str(committee).strip():
        return None
    if committee not in cache:
        match_type, fec_id, _name, _score = match_name(committee, name_index, buckets)
        cache[committee] = fec_id if match_type == "exact" else None
    return cache[committee]


def apply_fec_id(rec, name_index, buckets, cache):
    """Set rec['committee_fec_id'] from its committee. Returns True if it changed.

    The key is re-inserted at the record's end (after committee/committee_source)
    so diffs stay stable regardless of prior key order.
    """
    new_id = resolve_fec_id(rec.get("committee"), name_index, buckets, cache)
    changed = ("committee_fec_id" not in rec) or rec.get("committee_fec_id") != new_id
    rec.pop("committee_fec_id", None)
    rec["committee_fec_id"] = new_id
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--month", help="Limit to YYYY-MM (smoke test)")
    parser.add_argument("--limit-days", type=int, default=None)
    args = parser.parse_args()

    from fec_match import download_fec, load_fec_index

    download_fec()
    name_index, buckets = load_fec_index()

    day_files = iter_day_files()
    if args.month:
        y, m = args.month.split("-")
        day_files = [p for p in day_files if p.stem.startswith(f"{y}-{m}")]
    if args.limit_days:
        day_files = day_files[: args.limit_days]

    cache = {}
    counts = Counter()
    files_changed = 0

    for path in day_files:
        records = load_jsonl(path)
        file_changed = False
        for rec in records:
            if apply_fec_id(rec, name_index, buckets, cache):
                file_changed = True
            counts["matched" if rec["committee_fec_id"] else "unmatched"] += 1
            counts["records"] += 1
        if file_changed:
            files_changed += 1
            if not args.dry_run:
                save_jsonl(path, records)

    distinct_ids = len({v for v in cache.values() if v})
    verb = "would change" if args.dry_run else "changed"
    print(
        f"{counts['records']:,} records: {counts['matched']:,} with an FEC ID "
        f"({distinct_ids:,} distinct committees), {counts['unmatched']:,} without. "
        f"{files_changed:,} files {verb}."
    )


if __name__ == "__main__":
    main()
