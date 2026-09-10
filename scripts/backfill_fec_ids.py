"""Populate `committee_fec_id` on archive records from the FEC committee master.

Each record's `committee` name is resolved by fec_match.resolve_committee's
deterministic tier ladder (exact -> strip-unique -> subset-unique ->
acronym-unique -> cand-link), guarded by cycle and office-word checks.
Resolved IDs are stored as `committee_fec_id`; `ambiguous` names go to a
frequency-ordered review CSV and are NEVER auto-set, and fuzzy matches are
only ever a review hint elsewhere.

Idempotent: resolution is recomputed from `committee` each run, so re-running
keeps records in sync as committee names or the FEC cache change. Records
whose committee is not a federal committee (state/local) simply get None.

Artifacts (state/validation/, skipped on --dry-run):
  fec_review_candidates.csv  ambiguous names, frequency-ordered, with the
                             tier candidates for the registry review loop
  fec_tier_samples.csv       up to 100 sampled records per auto tier for the
                             >=98% precision spot-check

    uv run python scripts/backfill_fec_ids.py --dry-run       # report only
    uv run python scripts/backfill_fec_ids.py                 # write changes
    uv run python scripts/backfill_fec_ids.py --month 2026-08 # smoke test
"""

import argparse
import csv
import random
from collections import Counter

from committee_utils import iter_day_files
from utils import STATE_DIR, load_jsonl, save_jsonl

SAMPLES_PER_TIER = 100


def resolve_fec(committee, cycle, index, cache):
    """(tier, fec_id_or_None, candidates) for a committee name, memoized.

    The cache key includes the cycle: the same name on records from different
    cycles can resolve differently (multi-cycle disambiguation).
    """
    from fec_match import resolve_committee

    if not committee or not str(committee).strip():
        return (None, None, [])
    memo_key = (committee, cycle)
    if memo_key not in cache:
        record = {"date": f"{cycle}-01-01T00:00:00"} if cycle is not None else {}
        cache[memo_key] = resolve_committee(committee, record, index)
    return cache[memo_key]


def resolve_for_record(rec, index, cache):
    """resolve_fec for a record (cycle derived from its date)."""
    from fec_match import record_cycle

    return resolve_fec(rec.get("committee"), record_cycle(rec), index, cache)


def apply_fec_id(rec, index, cache):
    """Resolve and set rec['committee_fec_id']. Returns (changed, tier, fid).

    The key is re-inserted at the record's end (after committee/committee_source)
    so diffs stay stable regardless of prior key order. Review-only tiers and
    ambiguous/none resolve to None (review candidates are never auto-set).
    """
    from fec_match import AUTO_TIERS, REVIEW_ONLY_TIERS

    tier, fid, _cands = resolve_for_record(rec, index, cache)
    if tier not in AUTO_TIERS or tier in REVIEW_ONLY_TIERS:
        fid = None
    changed = ("committee_fec_id" not in rec) or rec.get("committee_fec_id") != fid
    rec.pop("committee_fec_id", None)
    rec["committee_fec_id"] = fid
    return changed, tier, fid


def spot_check_sample(rows_by_tier, seen_by_tier, tier, rec, committee, fid, index, rng):
    """Deterministic per-tier reservoir sample (records) for the spot check."""
    item = {
        "tier": tier,
        "unique_id": rec.get("unique_id"),
        "date": rec.get("date"),
        "email": rec.get("email"),
        "committee": committee,
        "fec_id": fid,
        "fec_name": index["meta"][fid]["name"] if fid in index["meta"] else "",
    }
    seen_by_tier[tier] = seen_by_tier.get(tier, 0) + 1
    rows = rows_by_tier.setdefault(tier, [])
    if len(rows) < SAMPLES_PER_TIER:
        rows.append(item)
    else:
        j = rng.randrange(seen_by_tier[tier])
        if j < SAMPLES_PER_TIER:
            rows[j] = item


def write_samples_csv(path, rows_by_tier, tiers):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "tier", "unique_id", "date", "email", "committee", "fec_id", "fec_name"])
        w.writeheader()
        for tier in tiers:
            for item in rows_by_tier.get(tier, []):
                w.writerow(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--month", help="Limit to YYYY-MM (smoke test)")
    parser.add_argument("--limit-days", type=int, default=None)
    parser.add_argument("--review-csv", default=None,
                        help="Path for the ambiguous-names review CSV")
    parser.add_argument("--samples-csv", default=None,
                        help="Path for the per-tier spot-check sample CSV")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    from fec_match import AUTO_TIERS, download_fec, load_fec_index

    download_fec()
    index, _buckets = load_fec_index()
    review_dir = STATE_DIR / "validation"

    day_files = iter_day_files()
    if args.month:
        y, m = args.month.split("-")
        day_files = [p for p in day_files if p.stem.startswith(f"{y}-{m}")]
    if args.limit_days:
        day_files = day_files[: args.limit_days]

    cache = {}
    counts = Counter()
    tier_counts = Counter()
    ambiguous = {}  # committee -> {"count": n, "tier": t, "cands": [...]}
    rows_by_tier = {}
    seen_by_tier = {}
    rng = random.Random(args.seed)
    files_changed = 0

    for path in day_files:
        records = load_jsonl(path)
        file_changed = False
        for rec in records:
            committee = rec.get("committee")
            changed, tier, fid = apply_fec_id(rec, index, cache)
            if changed:
                file_changed = True
            if committee:
                tier_counts[tier] += 1
                if fid:
                    counts["matched"] += 1
                    spot_check_sample(rows_by_tier, seen_by_tier, tier, rec,
                                      committee, fid, index, rng)
                else:
                    counts["unmatched"] += 1
                    if tier != "none":
                        entry = ambiguous.setdefault(
                            committee, {"count": 0, "tier": tier,
                                        "cands": resolve_for_record(
                                            rec, index, cache)[2]})
                        entry["count"] += 1
            counts["records"] += 1
        if file_changed:
            files_changed += 1
            if not args.dry_run:
                save_jsonl(path, records)

    distinct_ids = {fid for (_t, fid, _c) in cache.values() if fid}
    verb = "would change" if args.dry_run else "changed"
    print(f"{counts['records']:,} records: {counts['matched']:,} with an FEC ID "
          f"({len(distinct_ids):,} distinct committees), {counts['unmatched']:,} "
          f"without. {files_changed:,} files {verb}.")
    print("Tier ladder: " + ", ".join(f"{t}={tier_counts[t]:,}" for t in AUTO_TIERS)
          + f", ambiguous={tier_counts['ambiguous']:,}, none={tier_counts['none']:,}")

    if not args.dry_run:
        review_dir.mkdir(parents=True, exist_ok=True)
        review_csv = args.review_csv or review_dir / "fec_review_candidates.csv"
        samples_csv = args.samples_csv or review_dir / "fec_tier_samples.csv"
        with open(review_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["committee", "record_count", "tier",
                        "candidate_ids", "candidate_names"])
            for committee, entry in sorted(ambiguous.items(),
                                           key=lambda kv: (-kv[1]["count"], kv[0])):
                names = [index["meta"][c]["name"] for c in entry["cands"]]
                w.writerow([committee, entry["count"], entry["tier"],
                            "|".join(entry["cands"]), "|".join(names)])
        write_samples_csv(samples_csv, rows_by_tier, AUTO_TIERS)
        print(f"  review queue: {len(ambiguous):,} names -> {review_csv}")
        print(f"  spot-check samples -> {samples_csv}")


if __name__ == "__main__":
    main()