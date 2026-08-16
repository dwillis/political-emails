"""One-time sweep that derives party from committees and adds party_source.

Two phases:
  A. Aggregate a committee -> (party, source) map from override CSV + FEC party
     field + committee-name keywords + committee-majority (independent-source
     labels only, so it is idempotent and free of feedback loops).
  B. Rewrite every record: apply the committee-derived party (filling nulls AND
     correcting contradictions), else tag the provenance of the existing party.

Adds `party_source` to every record. Idempotent, rewrites the tree once; commit
data separately from code.

    uv run python scripts/apply_party_fixes.py --month 2024-11 --dry-run
    uv run python scripts/apply_party_fixes.py --dry-run
    uv run python scripts/apply_party_fixes.py
"""

import argparse
from collections import Counter, defaultdict

from committee_utils import iter_day_files, norm_label
from party_utils import (
    build_committee_party_map,
    committee_name_party,  # noqa: F401 (kept for parity/testing imports)
    fold_party,
    load_candidate_name_index,
    load_fec_party_map,
    load_party_overrides,
    match_person_party,
)
from process_email import determine_party, load_domain_party_map
from utils import CONFIG_DIR, DATA_DIR, load_jsonl, save_jsonl

OVERRIDES_PATH = CONFIG_DIR / "committee_party_overrides.csv"
DOMAIN_MAP_PATH = CONFIG_DIR / "domain_party_mapping.csv"
INDEPENDENT_SOURCES = {None, "human", "override", "fec", "fec-candidate",
                       "domain-map", "platform", "legacy"}


def aggregate():
    """Phase A: committee_counts and name-eligibility over the archive."""
    # norm_label -> [display_name, Counter(party from independent sources)]
    committee_counts = {}
    name_eligible = set()
    for path in iter_day_files():
        for rec in load_jsonl(path):
            c = rec.get("committee")
            if not c:
                continue
            norm = norm_label(c)
            entry = committee_counts.get(norm)
            if entry is None:
                entry = committee_counts[norm] = [c, Counter()]
            p = fold_party(rec.get("party"))
            if p and rec.get("party_source") in INDEPENDENT_SOURCES:
                entry[1][p] += 1
            if rec.get("committee_source") in ("disclaimer", "human"):
                name_eligible.add(norm)
    return committee_counts, name_eligible


def build_map(committee_counts, name_eligible):
    """Build the committee -> (party, source) map, plus FEC exact-match eligibility."""
    from fec_match import download_fec, load_fec_index, match_name
    download_fec()
    name_index, buckets = load_fec_index()
    fec_party_map = load_fec_party_map()
    exact_cache = {}

    def fec_lookup(norm):
        if norm not in exact_cache:
            mt, fid, _n, _s = match_name(norm, name_index, buckets)
            exact_cache[norm] = fid if mt == "exact" else None
        return exact_cache[norm]

    # A committee with an exact FEC match is also name-keyword eligible.
    for norm in committee_counts:
        if fec_lookup(norm):
            name_eligible.add(norm)

    overrides = load_party_overrides(OVERRIDES_PATH)
    return build_committee_party_map(
        committee_counts, fec_lookup, fec_party_map, overrides, name_eligible)


def make_record_fixer(committee_map, domain_map, cand_full, cand_initial):
    def candidate_party(rec):
        """Party from an FEC candidate named in the committee string or sender."""
        for text in (rec.get("committee"), rec.get("name")):
            if text:
                p = match_person_party(text, cand_full, cand_initial)
                if p:
                    return p
        return None

    def derive_fill(rec):
        """(party, source) to fill a null-party record, or (None, None).

        FEC candidate name first, then the domain map / platform (determine_party).
        Fill-only -- callers never let this overwrite an existing label.
        """
        cp = candidate_party(rec)
        if cp:
            return cp, "fec-candidate"
        # Domain map is body-independent (a candidate's domain implies party even
        # on a bodyless record); check it before the body-gated platform signal.
        dom = (rec.get("domain") or "").strip().lower()
        if dom in domain_map:
            return domain_map[dom], "domain-map"
        dp, ds = determine_party(rec.get("body") or "", rec.get("domain"),
                                 domain_map, rec.get("urls"))
        if dp:
            return dp, ds
        return None, None

    def reprovenance(rec, party):
        """Independent-source provenance of an existing label (never majority).

        Mirrors derive_fill's priority so a fec-candidate/domain-map fill re-tags
        to the same source on re-run (idempotency).
        """
        if candidate_party(rec) == party:
            return "fec-candidate"
        dom = (rec.get("domain") or "").strip().lower()
        if domain_map.get(dom) == party:
            return "domain-map"
        dp, ds = determine_party(rec.get("body") or "", rec.get("domain"),
                                 domain_map, rec.get("urls"))
        return ds if dp == party else "legacy"

    def fix_party_record(rec):
        tags = set()
        had_key = "party_source" in rec
        orig_source = rec.get("party_source")
        party = rec.get("party")
        committee = rec.get("committee")

        # 1. Fold junk stored party codes.
        folded = fold_party(party)
        if folded != party:
            party = folded
            tags.add("folded")

        if orig_source == "human":
            new_party, new_source = party, "human"
        else:
            derived = committee_map.get(norm_label(committee)) if committee else None
            dsrc = derived[1] if derived else None
            if derived and dsrc != "committee-majority":
                # override / fec / committee-name: authoritative; fills or corrects.
                new_party, new_source = derived
                if new_party != party:
                    tags.add("filled" if party is None else "corrected")
            elif derived and dsrc == "committee-majority":
                dp = derived[0]
                if party is None:
                    new_party, new_source = dp, "committee-majority"  # fill a null
                    tags.add("filled")
                elif orig_source == "committee-majority" and party == dp:
                    new_party, new_source = dp, "committee-majority"  # stable prior fill
                else:
                    # Existing independent label: keep it (majority never relabels),
                    # so it stays in the independent pool that computes the majority.
                    new_party, new_source = party, reprovenance(rec, party)
            elif party is not None:
                new_party, new_source = party, reprovenance(rec, party)
            else:
                # Null party, no committee-derived party: try candidate name /
                # domain map (fill-only).
                new_party, new_source = derive_fill(rec)
                if new_party is not None:
                    tags.add("filled")

        # Detect a provenance change even when the party value is unchanged
        # (e.g. an existing R record whose source becomes "fec").
        if had_key and new_source != rec.get("party_source"):
            tags.add("retagged")
        rec["party"] = new_party
        rec.pop("party_source", None)
        rec["party_source"] = new_source
        if not had_key:
            tags.add("source_key_added")
        return tags, party, new_party

    return fix_party_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--month", help="Limit to YYYY-MM")
    parser.add_argument("--limit-days", type=int, default=None)
    args = parser.parse_args()

    print("Phase A: aggregating committee party signals...")
    committee_counts, name_eligible = aggregate()
    committee_map = build_map(committee_counts, name_eligible)
    src_dist = Counter(s for _p, s in committee_map.values())
    print(f"  committees mapped: {len(committee_map):,}  by source: {dict(src_dist)}")

    domain_map = load_domain_party_map(DOMAIN_MAP_PATH)
    cand_full, cand_initial = load_candidate_name_index()
    print(f"  candidate name index: {len(cand_full):,} full, {len(cand_initial):,} initial")
    fix = make_record_fixer(committee_map, domain_map, cand_full, cand_initial)

    day_files = iter_day_files()
    if args.month:
        y, m = args.month.split("-")
        day_files = [p for p in day_files if p.stem.startswith(f"{y}-{m}")]
    if args.limit_days:
        day_files = day_files[: args.limit_days]

    counts = Counter()
    fills_by_source = Counter()
    files_changed = 0
    corrections = []
    null_before = null_after = 0

    for i, path in enumerate(day_files, 1):
        records = load_jsonl(path)
        changed = False
        for rec in records:
            tags, old_party, new_party = fix(rec)
            null_before += old_party is None
            null_after += new_party is None
            if old_party is None and new_party is not None:
                fills_by_source[rec.get("party_source")] += 1
            content = tags - {"source_key_added"}
            if content:
                changed = True
                for t in content:
                    counts[t] += 1
                if "corrected" in content and len(corrections) < 25:
                    corrections.append((rec.get("date"), rec.get("email"),
                                        rec.get("committee"), old_party, new_party))
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
    for tag in ("source_key_added", "filled", "corrected", "retagged", "folded"):
        print(f"  {tag:16} {counts[tag]:,}")
    print(f"  null party: {null_before:,} -> {null_after:,}")
    if fills_by_source:
        print("  fills by source: " + ", ".join(
            f"{s}={n:,}" for s, n in fills_by_source.most_common()))
    if corrections:
        print("\n  sample corrections (committee: old -> new):")
        for date, email, com, old, new in corrections[:15]:
            print(f"    {str(com)[:34]:34} {old} -> {new}   {email}")


if __name__ == "__main__":
    main()
