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
CAND_NM = 1     # cn.txt CAND_NAME ("LAST, FIRST MIDDLE")
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


# Tokens that are not part of a person's name (committee/sender boilerplate).
_PERSON_BOILERPLATE = {
    "team", "friends", "friend", "of", "for", "committee", "cmte", "elect",
    "reelect", "vote", "congress", "congressional", "senate", "senator", "sen",
    "governor", "gov", "president", "presidential", "campaign", "victory", "fund",
    "pac", "inc", "llc", "ltd", "the", "dr", "hon", "mr", "mrs", "ms", "rep",
    "us", "u", "s", "official", "hq", "headquarters", "esq",
    "van",  # lone "van" is noise; multiword surnames match on their last token
}
# jr/sr/ii/iii/iv are deliberately NOT stripped; a name ending in one of these
# suffixes is treated as unmatchable (see extract_person) rather than collapsing
# "Donald Trump Jr." onto Donald Trump (a father/son party mismatch risk).
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "hampshire", "jersey", "mexico",
    "york", "carolina", "dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode", "island", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "wisconsin", "wyoming",
}


def _person_tokens(text):
    """Lowercased alphabetic name tokens with boilerplate/state words removed."""
    s = text.lower()
    for sep in (" from ", " via ", " and ", " with ", " w/ "):
        idx = s.find(sep)
        if idx != -1:
            s = s[:idx]
    s = re.split(r'[("|“]', s)[0]      # drop org in parens/quotes/after pipe
    if " for " in s:                         # "Scott Jensen for Governor" -> before "for"
        s = s.split(" for ")[0]
    s = re.sub(r"[^a-z\s]", " ", s)
    return [t for t in s.split() if t not in _PERSON_BOILERPLATE and t not in _STATES]


def extract_person(text):
    """Guess (first, last) from a sender name or committee string, or None.

    Uses the first meaningful token as the given name and the LAST as the
    surname (so multi-word surnames like "Van Orden" match on "orden", matching
    how the candidate index is keyed). Returns None when fewer than two name
    tokens survive (e.g. "Team Kiggans").
    """
    if not text:
        return None
    tokens = _person_tokens(text)
    if len(tokens) < 2:
        return None
    if tokens[-1] in _NAME_SUFFIXES:
        return None  # "Donald Trump Jr." -> ambiguous, don't guess
    return (tokens[0], tokens[-1])


def load_candidate_name_index(years=CYCLES):
    """Return (full, initial) name indexes from cn.txt, party-bearing candidates.

    full:    {"first last": {folded_party, ...}}
    initial: {(last, first_initial): {folded_party, ...}}
    Both key the surname on its LAST token to align with extract_person.
    """
    full, initial = {}, {}
    for year in years:
        path = cn_path(year)
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as z, z.open("cn.txt") as f:
            for raw in f:
                parts = raw.decode("utf-8", "replace").rstrip("\n").split("|")
                if len(parts) <= CAND_PTY:
                    continue
                party = fold_party(parts[CAND_PTY])
                if not party:
                    continue
                name = parts[CAND_NM]
                if "," not in name:
                    continue
                surname_part, given_part = name.split(",", 1)
                sur = re.sub(r"[^a-z\s]", " ", surname_part.lower()).split()
                giv = re.sub(r"[^a-z\s]", " ", given_part.lower()).split()
                giv = [g for g in giv if g not in _PERSON_BOILERPLATE]
                if not sur or not giv:
                    continue
                last, first = sur[-1], giv[0]
                full.setdefault(f"{first} {last}", set()).add(party)
                initial.setdefault((last, first[0]), set()).add(party)
    return full, initial


def match_person_party(text, full, initial):
    """Party for a person named in `text`, or None. Never surname-only.

    Exact "first last" (unique party) wins; else first-initial + surname (unique
    party) as a gated fallback for nicknames ("Jen Kiggans" -> J. Kiggans).
    """
    person = extract_person(text)
    if not person:
        return None
    first, last = person
    parties = full.get(f"{first} {last}")
    if parties and len(parties) == 1:
        return next(iter(parties))
    parties = initial.get((last, first[0]))
    if parties and len(parties) == 1:
        return next(iter(parties))
    return None


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
