"""Cross-reference committee names against the FEC committee master file.

Downloads the FEC bulk committee master (cm.txt) and candidate master (cn.txt)
for the requested cycles and builds a cycle-aware, tiered index:

  exact     fec_match_key equality -- ALL committee IDs per name, not
            latest-wins (one registered name can belong to several real
            committees across cycles; callers pick by the record's cycle)
  stripped  trailing generic designators removed ("... PAC" == "... PAC, Inc.")
  subset    strict token subset (differing tokens all generic; cycle- and
            office-word guarded); looser relations are review-only
  acronym   single-token query matching a unique committee acronym (review-only)
  cand-link person match via the candidate master's CAND_ID linkage (review-only)

resolve_committee() walks that ladder deterministically for one record.
match_name() keeps the exact/fuzzy report behavior for validation signals.
Fuzzy difflib stays a review hint, NEVER persisted -- nothing here writes to
data/.

    uv run python scripts/fec_match.py --download          # refresh the cache
    uv run python scripts/fec_match.py                     # match archive values
"""

import argparse
import csv
import difflib
import re
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from committee_utils import iter_day_files, norm_label
from utils import DATA_DIR, load_jsonl

FEC_DIR = DATA_DIR.parent / "state" / "fec"
CYCLES = list(range(2016, 2028, 2))
# cm.txt field positions (verified against the 2026 bulk master).
CMTE_ID, CMTE_NM = 0, 1
CMTE_DSGN, CMTE_TP, CMTE_PTY = 8, 9, 10
CMTE_CAND = 14
# cn.txt field positions.
CAND_ID, CAND_NM, CAND_PTY, CAND_OFFICE = 0, 1, 2, 5
# 0.92 produced false positives on one-surname-swap names ("Harder for Congress"
# -> "HARPER FOR CONGRESS"). 0.95 keeps accent/"US Senate" normalizations only.
# Fuzzy matches are a review hint, NEVER a confirmation signal (see validate).
FUZZY_CUTOFF = 0.95

# Generic trailing designators: two names differing only by these tokens are
# variants of the same committee ("Upset The Setup PAC" vs "UPSET THE SETUP").
DESIGNATOR_TOKENS = {"pac", "committee", "inc", "llc", "ltd", "fund", "jfc"}

# Filler tokens that may differ between two spellings of the same committee.
# Used with DESIGNATOR_TOKENS to judge whether a subset relation is strict:
# extras that are all generic keep the match safe ("...US SENATE" vs
# "...SENATE"); non-generic extras (a person's first name, another word) put
# the pair in review-only territory ("MARJORIE TAYLOR GREENE FOR CONGRESS"
# vs "TAYLOR FOR CONGRESS").
_GENERIC_TOKENS = DESIGNATOR_TOKENS | {"for", "the", "us", "and"}

# Cycle tolerance for subset/cand-link guards: a committee must be registered
# within +/- this many cycles of the record's date (covers mail from just
# before a committee's first registration).
CYCLE_WINDOW = 2

# More than this many subset candidates means the query is too promiscuous to
# review usefully -- treat as no match.
SUBSET_MAX_CANDIDATES = 8

# Office vocabulary. A query naming a non-federal office never auto-matches a
# federal (H/S/P) committee ("Spanberger for Governor" != "SPANBERGER FOR
# CONGRESS"); a query naming a federal office must agree with the committee's
# own office type.
_STATE_OFFICE_RE = re.compile(
    r"\b(governor|gubernatorial|mayor|mayoral|assembly|legislature|legislative|"
    r"supervisor|sheriff|attorney|county|city|town|school|board)\b")
_FED_OFFICE_TYPES = {
    "H": {"congress", "congressional", "house"},
    "S": {"senate", "senatorial"},
    "P": {"president", "presidential"},
}

# Tiers resolve_committee may return, in ladder order. "subset-unique" is the
# STRICT subset class only (differing tokens all generic); the acronym,
# loose-subset and cand-link tiers were demoted to review-only after failing
# the plan's 100-sample >=98% precision spot-check: the loose subset class
# matched generic-token names ("American Action News" -> "ACTION!") and
# person-name supersets ("Marjorie Taylor Greene for Congress" -> "TAYLOR FOR
# CONGRESS"); cand-link linked the wrong committee for candidates with
# several across offices ("Vivek Ramaswamy for Ohio" -> his presidential
# committee); acronym-unique matched overloaded 3-letter initialisms by
# coincidence ("NRA" -> NATIONAL RESTAURANT ASSOCIATION, "LCV" -> LEFT COAST
# VOTER, "Put" -> POT USOA). Their candidates land in the review CSV via
# the tier they resolved at.
AUTO_TIERS = ("exact", "strip-unique", "subset-unique", "acronym-unique",
              "cand-link")
REVIEW_ONLY_TIERS = {"acronym-unique", "cand-link"}


def cm_url(year):
    return f"https://www.fec.gov/files/bulk-downloads/{year}/cm{str(year)[2:]}.zip"


def cm_path(year):
    return FEC_DIR / f"cm{str(year)[2:]}.zip"


def cn_url(year):
    return f"https://www.fec.gov/files/bulk-downloads/{year}/cn{str(year)[2:]}.zip"


def cn_path(year):
    return FEC_DIR / f"cn{str(year)[2:]}.zip"


def download_fec(years=CYCLES, refresh=False, candidates=True):
    """Download FEC committee master (cm) and, by default, candidate master (cn)."""
    FEC_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [("cm", cm_url, cm_path)]
    if candidates:
        jobs.append(("cn", cn_url, cn_path))
    for label, url_fn, path_fn in jobs:
        for year in years:
            path = path_fn(year)
            if path.exists() and not refresh:
                continue
            try:
                print(f"Downloading FEC {label}{str(year)[2:]}.zip ...")
                urllib.request.urlretrieve(url_fn(year), path)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] {label} {year} failed: {e} (continuing)")


def fec_match_key(value):
    """Equality key for FEC name matching (never replaces norm_label elsewhere).

    norm_label plus two committee-name conventions norm_label alone misses:
    an ampersand spelled out in some registered names ("SMITH & JONES" vs
    "SMITH AND JONES"), and dotted initialisms that norm_label splits into
    separate letters ("U.S. Senate" -> "u s senate" vs "US Senate" ->
    "us senate"). Runs of adjacent single-letter tokens are re-joined, so
    "u s" and "d n c" become "us" and "dnc"; lone initials ("John Q Public")
    are untouched. Keys BOTH the index and every lookup so the two sides
    agree; norm_label itself keeps its other duties (party-override CSV keys).
    """
    if value is None:
        return ""
    key = norm_label(str(value).replace("&", " and "))
    if not key:
        return ""
    words = key.split()
    out, run = [], []
    for w in words:
        if len(w) == 1:
            run.append(w)
            continue
        if run:
            out.append("".join(run))
            run = []
        out.append(w)
    if run:
        out.append("".join(run))
    return " ".join(out)


def strip_designators(key):
    """Drop trailing generic designator tokens ("x pac inc" -> "x")."""
    words = key.split()
    while words and words[-1] in DESIGNATOR_TOKENS:
        words.pop()
    return " ".join(words)


def record_cycle(record):
    """FEC cycle (year) for a record from its ISO date, or None."""
    d = (record.get("date") or "")[:4]
    return int(d) if d.isdigit() else None


def _acronym_of(key):
    """First-letter acronym of a multi-token key ("voter protection project"
    -> "vpp"); "" for single-token keys (their acronym is the token itself)."""
    words = key.split()
    if len(words) < 2:
        return ""
    return "".join(w[0] for w in words if w)


def build_index(cm_rows):
    """Pure index builder from parsed cm.txt rows (no zip/network in tests).

    cm_rows: (cycle, fec_id, name, dsgn, tp, party, cand_id)

    Returns an index dict with keys:
      exact      {match_key: set(fec_id)}        all IDs, not latest-wins
      stripped   {designator-stripped key: set(fec_id)}
      acronym    {acronym: set(fec_id)}
      meta       {fec_id: {name, dsgn, tp, party, cand_id, cycles, latest_cycle}}
      tokens     {token: set(fec_id)}            subset-tier postings
      token_sets {fec_id: frozenset(tokens)}
      fid_keys   {fec_id: [match_key, ...]}      for subset-tier order checks
    """
    exact, stripped, acronym = defaultdict(set), defaultdict(set), defaultdict(set)
    meta = {}
    tokens = defaultdict(set)
    token_sets = defaultdict(set)
    fid_keys = defaultdict(set)
    for cycle, fid, name, dsgn, tp, party, cand_id in cm_rows:
        key = fec_match_key(name)
        if not key:
            continue
        m = meta.setdefault(fid, {
            "name": name, "dsgn": dsgn, "tp": tp, "party": party,
            "cand_id": cand_id, "cycles": [], "latest_cycle": 0,
        })
        if cycle and cycle not in m["cycles"]:
            m["cycles"].append(cycle)
        if cycle and cycle > m["latest_cycle"]:
            m["latest_cycle"] = cycle
            m["name"] = name  # latest cycle's spelling is the display name
        exact[key].add(fid)
        stripped[strip_designators(key)].add(fid)
        ac = _acronym_of(key)
        if len(ac) >= 2:
            acronym[ac].add(fid)
        token_sets[fid].update(key.split())
        fid_keys[fid].add(key)
    for fid, toks in token_sets.items():
        for t in toks:
            tokens[t].add(fid)
    return {
        "exact": dict(exact),
        "stripped": dict(stripped),
        "acronym": dict(acronym),
        "meta": meta,
        "tokens": dict(tokens),
        "token_sets": {fid: frozenset(t) for fid, t in token_sets.items()},
        "fid_keys": {fid: sorted(k) for fid, k in fid_keys.items()},
    }


def _cn_person(name):
    """cn.txt CAND_NAME ("LAST, FIRST MIDDLE") -> "first last", or None.

    Aligns with party_utils.extract_person: the surname is keyed on its LAST
    token ("VAN ORDEN" -> "orden") and suffix-bearing names ("... JR") are
    rejected so fathers are not matched to sons.
    """
    if not name or "," not in name:
        return None
    surname_part, given_part = name.split(",", 1)

    def toks(s):
        return [t for t in re.sub(r"[^a-z\s]", " ", s.lower()).split() if t]

    sur, giv = toks(surname_part), toks(given_part)
    if not sur or not giv:
        return None
    if sur[-1] in {"jr", "sr", "ii", "iii", "iv"}:
        return None
    return f"{giv[0]} {sur[-1]}"


def build_cand_index(cand_rows):
    """{"first last": set(cand_id)} from parsed cn.txt rows."""
    cand_names = defaultdict(set)
    for cand_id, name, party, office in cand_rows:
        person = _cn_person(name)
        if person:
            cand_names[person].add(cand_id)
    return dict(cand_names)


def _cand_committees(cm_rows):
    """{cand_id: set(fec_id)} linkage from cm.txt CAND_ID."""
    out = defaultdict(set)
    for _cycle, fid, _name, _d, _t, _p, cand_id in cm_rows:
        if cand_id:
            out[cand_id].add(fid)
    return dict(out)


def load_fec_index(years=CYCLES):
    """Return (index, buckets).

    index: see build_index (plus "cand_names" and "cand_committees").
    buckets: {(first_char, len//5): [key, ...]} for fuzzy candidate lookup.
    """
    cm_rows, cn_rows = [], []
    for year in years:
        path = cm_path(year)
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as z:
            with z.open("cm.txt") as f:
                for raw in f:
                    parts = raw.decode("utf-8", "replace").rstrip("\n").split("|")
                    if len(parts) <= CMTE_NM:
                        continue
                    cm_rows.append((
                        year, parts[CMTE_ID], parts[CMTE_NM],
                        parts[CMTE_DSGN] if len(parts) > CMTE_DSGN else "",
                        parts[CMTE_TP] if len(parts) > CMTE_TP else "",
                        parts[CMTE_PTY] if len(parts) > CMTE_PTY else "",
                        parts[CMTE_CAND] if len(parts) > CMTE_CAND else "",
                    ))
    for year in years:
        path = cn_path(year)
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as z:
            with z.open("cn.txt") as f:
                for raw in f:
                    parts = raw.decode("utf-8", "replace").rstrip("\n").split("|")
                    if len(parts) <= CAND_OFFICE:
                        continue
                    cn_rows.append((parts[CAND_ID], parts[CAND_NM],
                                    parts[CAND_PTY], parts[CAND_OFFICE]))
    index = build_index(cm_rows)
    index["cand_names"] = build_cand_index(cn_rows)
    index["cand_committees"] = _cand_committees(cm_rows)
    print(f"FEC index: {len(index['exact']):,} distinct names, "
          f"{len(index['meta']):,} committees from {len(cm_rows):,} cm rows")
    return index, _build_buckets(index)


def _build_buckets(index):
    buckets = defaultdict(list)
    for key in index["exact"]:
        buckets[(key[0], len(key) // 5)].append(key)
    return dict(buckets)


def _latest_id(index, fids):
    """Deterministically pick one committee from a set: latest cycle, then ID."""
    return sorted(fids, key=lambda f: (index["meta"][f]["latest_cycle"], f))[-1]


def match_name(value, index, buckets):
    """Match a committee value against the FEC index (report/validation signal).

    Returns (match_type, fec_id, matched_name, score). match_type is
    "exact" | "fuzzy" | "none". When a name maps to several real committees,
    the latest-cycle ID is returned for reporting; cycle-aware resolution of
    individual records is resolve_committee's job.
    """
    key = fec_match_key(value)
    if not key:
        return ("none", "", "", 0.0)
    ids = index["exact"].get(key)
    if ids:
        fid = _latest_id(index, ids)
        return ("exact", fid, index["meta"][fid]["name"], 1.0)
    # Fuzzy within the same first-char + length bucket (keeps it tractable).
    candidates = buckets.get((key[0], len(key) // 5), [])
    close = difflib.get_close_matches(key, candidates, n=1, cutoff=FUZZY_CUTOFF)
    if close:
        best = close[0]
        score = difflib.SequenceMatcher(None, key, best).ratio()
        fid = _latest_id(index, index["exact"][best])
        return ("fuzzy", fid, index["meta"][fid]["name"], round(score, 3))
    return ("none", "", "", 0.0)


def _cycle_distance(index, fid, cycle):
    """Distance from `cycle` to the committee's nearest registration cycle."""
    cycles = index["meta"][fid]["cycles"]
    if not cycles:
        return 999
    if cycle is None:
        return 0
    return min(abs(c - cycle) for c in cycles)


def _cycle_filter(index, fids, cycle):
    """Fids tied for nearest registered cycle (cycle-ambiguity reduction)."""
    if cycle is None:
        return sorted(fids)
    best = min(_cycle_distance(index, f, cycle) for f in fids)
    return sorted(f for f in fids if _cycle_distance(index, f, cycle) == best)


def _office_conflicts(key, tp):
    """True if the query's office words disagree with the committee's office.

    A query with no federal office word passes (vacuous); a query naming a
    federal office must match the committee's H/S/P type. Other committee
    types (PACs, JFCs, ...) have no office to disagree with.
    """
    if tp not in _FED_OFFICE_TYPES:
        return False
    qfed = set(key.split()) & {"president", "presidential", "senate",
                               "senatorial", "congress", "congressional", "house"}
    if not qfed:
        return False
    return not (qfed & _FED_OFFICE_TYPES[tp])


def _passes_guards(index, fid, key, cycle):
    """Cycle-window and office-word guards for subset/cand-link candidates."""
    m = index["meta"][fid]
    if _office_conflicts(key, m["tp"]):
        return False
    if cycle is not None:
        cycles = m["cycles"]
        if not cycles:
            return False
        if cycle < min(cycles) - CYCLE_WINDOW or cycle > max(cycles) + CYCLE_WINDOW:
            return False
    return True


def _is_generic_token(token):
    """Generic/filler token or a bare number (a year or cycle suffix)."""
    return token in _GENERIC_TOKENS or token.isdigit()


def _ordered_containment(short, long_):
    """True if the token sequence `short` is an order-preserving subsequence
    of `long_` (same relative order, gaps allowed)."""
    it = iter(long_)
    return all(tok in it for tok in short)


def _subset_candidates(key, index, cycle):
    """FEC committees related to the query by a token-subset relation.

    Both directions: the query is a token subset of a registered name ("Rand
    Paul for Senate" ⊆ "RAND PAUL FOR US SENATE") or a superset ("Upset The
    Setup PAC" ⊇ "UPSET THE SETUP"). Each candidate must pass the cycle and
    office guards; unguarded matches are dropped, not counted.

    Returns (strict, loose). strict candidates differ from the query only in
    generic tokens (designators, filler, bare years) -- safe to auto-apply.
    loose candidates contain non-generic extras (a dropped first name, a
    person-name overlap) -- the class that failed the precision spot-check,
    so they are review-only and land in "ambiguous".
    """
    qtokens = frozenset(key.split())
    postings = index["tokens"]
    token_sets = index["token_sets"]

    # Direction A: query ⊆ FEC (the fid's name contains every query token).
    lists = sorted((postings[t] for t in qtokens if t in postings), key=len)
    base = set()
    if len(lists) == len(qtokens):
        base = set(lists[0])
        for s in lists[1:]:
            base &= s
            if not base:
                break

    # Direction B: FEC ⊆ query (all of the fid's tokens appear in the query).
    # Enumerate via the query's non-generic tokens; a name made only of
    # generic tokens is not a real committee.
    sup = set()
    for t in qtokens:
        if not _is_generic_token(t):
            sup |= postings.get(t, set())

    strict, loose = [], []
    qseq = key.split()
    fid_keys = index.get("fid_keys", {})
    for fid in base | sup:
        ftoks = token_sets[fid]
        if not (ftoks <= qtokens or qtokens <= ftoks):
            continue
        if not _passes_guards(index, fid, key, cycle):
            continue
        extra = qtokens - ftoks if ftoks <= qtokens else ftoks - qtokens
        # Order must agree too: same tokens in a different order is a
        # different name, not a variant ("United American Patriots" is not
        # "AMERICAN PATRIOTS UNITED").
        ordered = any(
            _ordered_containment(qseq, fseq) if qtokens <= ftoks
            else _ordered_containment(fseq, qseq)
            for fseq in (s.split() for s in fid_keys.get(fid, ()))
        )
        if all(_is_generic_token(t) for t in extra) and ordered:
            strict.append(fid)
        else:
            loose.append(fid)
    return strict, loose


def resolve_committee(value, record, index):
    """Deterministic tier ladder for one committee label.

    Returns (tier, fec_id, candidates). Tiers, first hit wins:

      exact          collapsed key matches (cycle-aware among committees that
                     share a registered name)
      strip-unique   trailing designators stripped, unique guarded committee
      subset-unique  strict token-subset relation (extras all generic), unique
      acronym-unique single-token query matching a unique committee acronym
                     (REVIEW-ONLY: 3-letter initialisms are overloaded -- see
                     REVIEW_ONLY_TIERS)
      cand-link      person match via the candidate master (REVIEW-ONLY)
      ambiguous      one or more candidates for review (never auto-set): loose
                     subset relations, or multiple candidates at a tier
      none

    Multiple candidates at any tier stop the ladder: a near-exact name that
    names two real committees stays ambiguous rather than being overridden by
    a sloppier tier.
    """
    from party_utils import committee_name_party, extract_person  # avoids import cycle

    key = fec_match_key(value)
    if not key:
        return ("none", None, [])
    cycle = record_cycle(record)

    ids = index["exact"].get(key)
    if ids:
        if len(ids) == 1:
            return ("exact", next(iter(ids)), [])
        cands = _cycle_filter(index, ids, cycle)
        if len(cands) == 1:
            return ("exact", cands[0], [])
        return ("ambiguous", None, sorted(ids))

    skey = strip_designators(key)
    if skey and skey != key:
        ids = index["stripped"].get(skey)
        if ids:
            # Several committees share the stripped name -> the variant is
            # inherently ambiguous (a cycle window is not proof one of them is
            # not the referent; "Save America JFC" vs two SAVE AMERICA
            # committees). Stop the ladder here, deterministically.
            if len(ids) > 1:
                return ("ambiguous", None, sorted(ids))
            if _passes_guards(index, next(iter(ids)), key, cycle):
                return ("strip-unique", next(iter(ids)), [])

    # Subset relations need at least two query tokens: a single token is a
    # subset of far too many names ("PAC" ⊆ "AMERICA PAC"). Only the strict
    # class auto-applies; loose candidates are review-only ("ambiguous").
    if len(key.split()) >= 2:
        strict, loose = _subset_candidates(key, index, cycle)
        if len(strict) == 1:
            return ("subset-unique", strict[0], [])
        if 1 < len(strict) <= SUBSET_MAX_CANDIDATES:
            return ("ambiguous", None, sorted(strict))
        if 1 <= len(loose) <= SUBSET_MAX_CANDIDATES:
            return ("ambiguous", None, sorted(loose))

    if " " not in key and len(key) >= 2:
        ids = index["acronym"].get(key)
        if ids:
            if len(ids) == 1:
                return ("acronym-unique", next(iter(ids)), [])
            return ("ambiguous", None, sorted(ids))

    # cand-link: a person's name plus CAND_ID-linked committees. Queries
    # naming a state/local office ("... for Governor") never link to a
    # federal candidate.
    person = extract_person(value)
    if person and not _STATE_OFFICE_RE.search(key):
        cids = index["cand_names"].get(f"{person[0]} {person[1]}", set())
        fids = set()
        for cid in cids:
            fids |= index["cand_committees"].get(cid, set())
        if fids:
            qparty = committee_name_party(value)
            if qparty:
                fids = [f for f in fids
                        if _fold(index["meta"][f]["party"]) in (None, qparty)]
            kept = [f for f in sorted(fids) if _passes_guards(index, f, key, cycle)]
            if len(kept) == 1:
                return ("cand-link", kept[0], [])
            if 1 < len(kept) <= SUBSET_MAX_CANDIDATES:
                return ("ambiguous", None, sorted(kept))

    return ("none", None, [])


def _fold(code):
    """Fold an FEC CMTE_PTY code to D/R/I/G (lazy import avoids a cycle)."""
    from party_utils import fold_party
    return fold_party(code)


def archive_value_counts():
    counts = Counter()
    for path in iter_day_files():
        for rec in load_jsonl(path):
            c = rec.get("committee")
            if c:
                counts[c] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download/refresh the FEC cache and exit")
    parser.add_argument("--refresh", action="store_true", help="Force re-download")
    parser.add_argument("--out", type=Path, default=FEC_DIR / "fec_matches.csv")
    args = parser.parse_args()

    if args.download:
        download_fec(refresh=True)
        return

    download_fec(refresh=args.refresh)
    index, buckets = load_fec_index()
    if not index["exact"]:
        print("No FEC data cached. Run with --download first.")
        return

    counts = archive_value_counts()
    print(f"Matching {len(counts):,} distinct archive committee values...")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    exact = fuzzy = none = 0
    rec_exact = 0
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["committee", "record_count", "match_type", "fec_id", "matched_name", "score"])
        for value, n in counts.most_common():
            mt, fid, mname, score = match_name(value, index, buckets)
            exact += mt == "exact"
            fuzzy += mt == "fuzzy"
            none += mt == "none"
            if mt == "exact":
                rec_exact += n
            w.writerow([value, n, mt, fid, mname, score])

    print(f"  exact: {exact:,} values  fuzzy: {fuzzy:,}  none: {none:,}  (of {len(counts):,})")
    print(f"  records under an exact FEC match: {rec_exact:,}")
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()