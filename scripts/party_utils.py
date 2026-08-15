"""Party (partisan affiliation) derivation from committees and FEC data.

Pure helpers (CSV/zip readers only, no network) that map a committee to a party
using, in precedence order: a curated override CSV, the FEC committee master's
party field, committee-name keywords, and committee-majority propagation.

Party codes are folded to D / R / I / G; everything else -> None.
"""

import csv
import re
import zipfile

from committee_utils import norm_label
from fec_match import CMTE_NM, CYCLES, cm_path, cn_path

# FEC field positions.
CMTE_PTY = 10   # cm.txt CMTE_PTY_AFFILIATION
CMTE_CAND = 14  # cm.txt CAND_ID (linkage to the candidate master)
CAND_PTY = 2    # cn.txt CAND_PTY_AFFILIATION
FEC_PARTY_FOLD = {
    "DEM": "D", "DFL": "D",
    "REP": "R", "GOP": "R",
    "IND": "I", "IDP": "I",
    "GRE": "G",
}
PARTIES = {"D", "R", "I", "G"}

_DEM_RE = re.compile(r"\b(democrat(?:s|ic)?|dems?|dnc|dccc|dscc|dlcc|dga)\b")
_REP_RE = re.compile(r"\b(republicans?|gop|rnc|nrcc|nrsc|rga|rslc)\b")
# Oppositional names ("Stop Republicans PAC") name the party they OPPOSE, so a
# bare keyword match is misleading -- skip them and let majority voting decide.
_OPPOSITIONAL_RE = re.compile(
    r"\b(stop|defeat|against|anti|beat|dump|fire|oppose|ban|no more|end)\b")


def fold_party(value):
    """Fold an FEC code or stored value to D/R/I/G, or None."""
    if not value:
        return None
    v = str(value).strip().upper()
    if v in PARTIES:
        return v
    return FEC_PARTY_FOLD.get(v)


def committee_name_party(name):
    """Party from committee-name keywords, or None. Both-party match -> None."""
    n = norm_label(name)
    if not n or _OPPOSITIONAL_RE.search(n):
        return None
    d, r = bool(_DEM_RE.search(n)), bool(_REP_RE.search(n))
    if d and not r:
        return "D"
    if r and not d:
        return "R"
    return None


def majority_party(counter, min_records=20, min_share=0.95):
    """Dominant party in a Counter of party labels, or None.

    I/G stay in the denominator so a genuinely split committee is ambiguous.
    """
    total = sum(counter.values())
    if total < min_records:
        return None
    party, n = counter.most_common(1)[0]
    if party in PARTIES and n / total >= min_share:
        return party
    return None


def _rank(party):
    """Preference rank across cycles: major party > minor party > none."""
    return 2 if party in ("D", "R") else 1 if party in ("I", "G") else 0


def _keep(new, current, seen):
    """Accumulate the preferred party across cycles: highest rank, tie -> latest."""
    return (not seen) or _rank(new) >= _rank(current)


def _load_candidate_party(years):
    """cn.txt: {cand_id: folded_party}, preferring a major-party cycle.

    A candidate who re-registers third-party for one cycle (e.g. Ty Pinkins:
    DEM 2022/2024, IND 2026) keeps the major-party affiliation, since this
    archive's senders are overwhelmingly D/R and a single off-cycle registration
    shouldn't overwrite it. True third-party candidates never have a D/R cycle.
    """
    cand = {}
    for year in years:
        path = cn_path(year)
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as z, z.open("cn.txt") as f:
            for raw in f:
                parts = raw.decode("utf-8", "replace").rstrip("\n").split("|")
                if len(parts) <= CAND_PTY:
                    continue
                cid = parts[0]
                p = fold_party(parts[CAND_PTY])
                if _keep(p, cand.get(cid), cid in cand):
                    cand[cid] = p
    return cand


def load_fec_party_map(years=CYCLES):
    """Return {fec_id: folded_party|None} for FEC committees.

    For candidate committees the linked candidate's registered party (cn.txt via
    CAND_ID) takes precedence, because a committee's own CMTE_PTY_AFFILIATION is
    unreliable there (e.g. "Ty Pinkins for Congress" registers IND but the
    candidate is DEM). Non-candidate committees fall back to the committee party.
    """
    cand_party = _load_candidate_party(years)
    out = {}
    for year in years:
        path = cm_path(year)
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as z, z.open("cm.txt") as f:
            for raw in f:
                parts = raw.decode("utf-8", "replace").rstrip("\n").split("|")
                if len(parts) <= CMTE_PTY:
                    continue
                fid = parts[0]
                cand_id = parts[CMTE_CAND] if len(parts) > CMTE_CAND else ""
                party = cand_party.get(cand_id) if cand_id else None
                if not party:
                    party = fold_party(parts[CMTE_PTY])
                if _keep(party, out.get(fid), fid in out):
                    out[fid] = party
    return out


def load_party_overrides(path):
    """Return {norm_label: 'D'|'R'|'I'|'G'|'NONE'} from a committee override CSV.

    'NONE' blocks all automatic derivation for that committee.
    """
    overrides = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = norm_label(row.get("committee"))
            val = (row.get("party") or "").strip().upper()
            if name and (val in PARTIES or val == "NONE"):
                overrides[name] = val
    return overrides


def derive_committee_party(committee, fill_source, fec_lookup, fec_party_map, overrides):
    """Single-record party derivation for enrichment time (no majority signal).

    Precedence: override > FEC > committee-name (only when the committee came
    from a disclaimer/human source). Returns (party, source) or (None, None).
    """
    norm = norm_label(committee)
    if not norm:
        return (None, None)
    ov = overrides.get(norm)
    if ov == "NONE":
        return (None, None)
    if ov in PARTIES:
        return (ov, "override")
    fid = fec_lookup(norm)
    fec_party = fec_party_map.get(fid) if fid else None
    if fec_party:
        return (fec_party, "fec")
    if fill_source in ("disclaimer", "human"):
        name_party = committee_name_party(committee)
        if name_party:
            return (name_party, "committee-name")
    return (None, None)


def build_committee_party_map(committee_counts, fec_lookup, fec_party_map,
                              overrides, name_eligible):
    """Map norm_label(committee) -> (party, source).

    Precedence per committee: override > fec > committee-name (gated by
    name_eligible) > committee-majority. `committee_counts` maps norm_label ->
    (display_name, party_counter). `fec_lookup` maps norm_label -> fec_id|None
    (exact matches only). Returns only committees that resolve to a party.
    """
    out = {}
    for norm, (display, counter) in committee_counts.items():
        ov = overrides.get(norm)
        if ov == "NONE":
            continue
        if ov in PARTIES:
            out[norm] = (ov, "override")
            continue
        fid = fec_lookup(norm)
        fec_party = fec_party_map.get(fid) if fid else None
        if fec_party:
            out[norm] = (fec_party, "fec")
            continue
        if norm in name_eligible:
            name_party = committee_name_party(display)
            if name_party:
                out[norm] = (name_party, "committee-name")
                continue
        maj = majority_party(counter)
        if maj:
            out[norm] = (maj, "committee-majority")
    return out
