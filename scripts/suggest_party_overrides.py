"""Generate party-override suggestions for the remaining party-null records.

Emits state/validation/party_suggestions.csv for hand review: the highest-volume
party-null committees and domains, each tagged with evidence for how it might be
resolved. You paste accepted committee rows into
config/committee_party_overrides.csv and domain rows into
config/domain_party_mapping.csv, then rerun scripts/apply_party_fixes.py (the
domain map fills retroactively).

    uv run python scripts/suggest_party_overrides.py [--min-records 25]

Evidence values:
  fec-blank-party        exact FEC committee match but no derivable party (JFC /
                         leadership PAC / c4) -- needs a hand call.
  state-candidate-shaped looks like a candidate/committee not in the federal FEC
                         files (state races: governor, SoS, AG, mayor, ...).
  org                    media / advocacy org; usually legitimately party-null.
  domain-consensus       a domain whose labeled mail is >=90% one party.
"""

import argparse
import csv
import re
from collections import Counter, defaultdict

from committee_utils import iter_day_files, norm_label
from utils import DATA_DIR, load_jsonl

OUT_PATH = DATA_DIR.parent / "state" / "validation" / "party_suggestions.csv"

_STATE_OFFICE_RE = re.compile(
    r"\bfor\b.*\b(governor|secretary of state|attorney general|lieutenant governor"
    r"|lt governor|mayor|assembly|state (?:senate|house|rep)|city council|county"
    r"|school board|comptroller|treasurer|auditor|supreme court|legislature)\b",
    re.IGNORECASE,
)
_CAND_SHAPED_RE = re.compile(
    r"\b(for (?:congress|senate|governor|president|mayor|assembly)|friends of"
    r"|team |elect |re-?elect |vote )",
    re.IGNORECASE,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-records", type=int, default=25)
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    null_committee = Counter()
    null_domain = Counter()
    domain_party = defaultdict(Counter)   # domain -> Counter(party) over LABELED records
    for path in iter_day_files():
        for rec in load_jsonl(path):
            party = rec.get("party")
            dom = (rec.get("domain") or "").strip().lower()
            if party is None:
                c = rec.get("committee")
                if c:
                    null_committee[c] += 1
                if dom:
                    null_domain[dom] += 1
            elif dom:
                domain_party[dom][party] += 1

    # FEC committee index for evidence.
    from fec_match import download_fec, load_fec_index, match_name
    from party_utils import load_fec_party_map
    download_fec()
    name_index, buckets = load_fec_index()
    fec_party_map = load_fec_party_map()

    def committee_evidence(name):
        mt, fid, mname, _score = match_name(norm_label(name), name_index, buckets)
        if mt == "exact":
            if fid.startswith("C9"):     # communication-cost filer, never party-coded
                return ("org", fid, mname)
            if not fec_party_map.get(fid):
                return ("fec-blank-party", fid, mname)
        if _STATE_OFFICE_RE.search(name) or _CAND_SHAPED_RE.search(name):
            return ("state-candidate-shaped", "", "")
        return ("org", "", "")

    rows = []
    for name, n in null_committee.most_common():
        if n < args.min_records:
            break
        evidence, fid, mname = committee_evidence(name)
        rows.append(["committee", name, n, evidence, fid, mname, ""])

    for dom, n in null_domain.most_common():
        if n < args.min_records:
            break
        labeled = domain_party.get(dom)
        suggested = ""
        evidence = "domain-null"
        if labeled:
            top, tc = labeled.most_common(1)[0]
            if tc / sum(labeled.values()) >= 0.90:
                evidence, suggested = "domain-consensus", top
        rows.append(["domain", dom, n, evidence, "", "", suggested])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "key", "records", "evidence", "fec_id", "fec_name", "suggested_party"])
        w.writerows(rows)

    by_ev = Counter(r[3] for r in rows if r[0] == "committee")
    print(f"wrote {args.out}: {len(rows)} rows "
          f"({sum(1 for r in rows if r[0]=='committee')} committees, "
          f"{sum(1 for r in rows if r[0]=='domain')} domains)")
    print("  committee evidence:", dict(by_ev.most_common()))


if __name__ == "__main__":
    main()
