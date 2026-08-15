"""Tests for party derivation helpers."""

from collections import Counter

import pytest

from party_utils import (
    build_committee_party_map,
    committee_name_party,
    derive_committee_party,
    fold_party,
    majority_party,
)


@pytest.mark.parametrize(
    "code,expected",
    [("DEM", "D"), ("DFL", "D"), ("REP", "R"), ("GOP", "R"), ("IND", "I"),
     ("GRE", "G"), ("R", "R"), ("dem", "D"), ("LIB", None), ("UNK", None),
     ("", None), (None, None)],
)
def test_fold_party(code, expected):
    assert fold_party(code) == expected


@pytest.mark.parametrize(
    "name,expected",
    [("Democratic Party of Georgia", "D"), ("NRCC", "R"), ("Team GOP", "R"),
     ("Democrats for Congress", "D"), ("Trump Train News", None),
     ("Republicans vs Democrats PAC", None),  # both -> ambiguous
     ("Stop Republicans PAC", None),          # oppositional
     ("Defeat the Democrats", None),
     ("Modern Democracy Fund", None)],        # "democracy" not "democrat"
)
def test_committee_name_party(name, expected):
    assert committee_name_party(name) == expected


def test_majority_party():
    assert majority_party(Counter({"D": 19})) is None            # below min_records
    assert majority_party(Counter({"D": 20})) == "D"             # 100%
    assert majority_party(Counter({"D": 19, "R": 1})) == "D"     # 95%
    assert majority_party(Counter({"D": 18, "R": 2})) is None    # 90% < 95%
    assert majority_party(Counter({"D": 30, "G": 10})) is None   # I/G stay in denominator


def test_build_map_precedence():
    counts = {
        "a pac": ("A PAC", Counter({"D": 50})),        # majority D
        "nrcc": ("NRCC", Counter()),                    # name R (eligible)
        "override co": ("Override Co", Counter({"D": 99})),
        "blocked co": ("Blocked Co", Counter({"R": 99})),
    }
    fec_party = {"F1": "R"}
    fec_lookup = lambda n: "F1" if n == "a pac" else None  # noqa: E731
    overrides = {"override co": "D", "blocked co": "NONE"}
    m = build_committee_party_map(counts, fec_lookup, fec_party, overrides,
                                  name_eligible={"nrcc"})
    assert m["a pac"] == ("R", "fec")            # fec beats majority
    assert m["nrcc"] == ("R", "committee-name")  # name (eligible)
    assert m["override co"] == ("D", "override")
    assert "blocked co" not in m                 # NONE blocks derivation


def test_name_eligibility_gate():
    # a media-style committee, not eligible, no FEC -> absent from map
    counts = {"republican daily news": ("Republican Daily News", Counter({"D": 3}))}
    m = build_committee_party_map(counts, lambda n: None, {}, {}, name_eligible=set())
    # not name-eligible and majority below threshold -> not mapped
    assert "republican daily news" not in m


def test_derive_committee_party():
    fec_lookup = lambda n: "F1" if n == "nrsc" else None  # noqa: E731
    fec_party = {"F1": "R"}
    overrides = {"emily s list": "D", "block me": "NONE"}
    assert derive_committee_party("NRSC", "llm:x", fec_lookup, fec_party, overrides) == ("R", "fec")
    assert derive_committee_party("EMILY's List", "llm:x", fec_lookup, fec_party, overrides) == ("D", "override")
    assert derive_committee_party("Block Me", "disclaimer", fec_lookup, fec_party, overrides) == (None, None)
    # name keyword only when the committee came from a disclaimer
    assert derive_committee_party("Democratic Party of Ohio", "disclaimer", lambda n: None, {}, {}) == ("D", "committee-name")
    assert derive_committee_party("Democratic Party of Ohio", "llm:x", lambda n: None, {}, {}) == (None, None)
