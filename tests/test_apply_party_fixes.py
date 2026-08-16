"""Tests for the party sweep record fixer."""

from apply_party_fixes import make_record_fixer


def fixer(committee_map=None, domain_map=None, cand_full=None, cand_initial=None):
    return make_record_fixer(committee_map or {}, domain_map or {},
                             cand_full or {}, cand_initial or {})


def test_fill_from_committee_map():
    fix = fixer({"nrsc": ("R", "fec")})
    rec = {"committee": "NRSC", "party": None, "body": ""}
    tags, old, new = fix(rec)
    assert new == "R" and rec["party"] == "R" and rec["party_source"] == "fec"
    assert "filled" in tags


def test_correct_contradiction():
    fix = fixer({"democratic national committee": ("D", "fec")})
    rec = {"committee": "Democratic National Committee", "party": "R", "body": ""}
    tags, old, new = fix(rec)
    assert new == "D" and rec["party_source"] == "fec"
    assert "corrected" in tags


def test_human_preserved():
    fix = fixer({"nrsc": ("R", "fec")})
    rec = {"committee": "NRSC", "party": "D", "party_source": "human", "body": ""}
    fix(rec)
    assert rec["party"] == "D" and rec["party_source"] == "human"


def test_legacy_provenance_when_no_derivation():
    fix = fixer(committee_map={}, domain_map={})
    rec = {"committee": "Unknown Cmte", "party": "D", "body": "no signal", "urls": []}
    fix(rec)
    assert rec["party"] == "D" and rec["party_source"] == "legacy"


def test_domain_map_provenance():
    fix = fixer(committee_map={}, domain_map={"foo.com": "D"})
    rec = {"committee": None, "party": "D", "domain": "FOO.com", "body": "x", "urls": []}
    fix(rec)
    assert rec["party_source"] == "domain-map"


def test_null_party_source_null():
    fix = fixer()
    rec = {"committee": None, "party": None, "body": ""}
    fix(rec)
    assert rec["party"] is None and rec["party_source"] is None


def test_party_source_lands_last():
    fix = fixer({"nrsc": ("R", "fec")})
    rec = {"committee": "NRSC", "committee_source": "disclaimer", "party": None, "body": ""}
    fix(rec)
    assert list(rec.keys())[-1] == "party_source"


def test_idempotent():
    fix = fixer({"nrsc": ("R", "fec")})
    rec = {"committee": "NRSC", "party": None, "body": ""}
    fix(rec)
    tags2, _o, _n = fix(rec)
    assert tags2 - {"source_key_added"} == set()  # no content change on rerun
    assert "source_key_added" not in tags2         # key already present


def test_fill_from_candidate_name():
    # committee not in committee_map, but sender name matches a candidate
    fix = fixer(cand_full={"jane doe": {"D"}}, cand_initial={("doe", "j"): {"D"}})
    rec = {"committee": None, "name": "Jane Doe", "party": None, "body": ""}
    tags, old, new = fix(rec)
    assert new == "D" and rec["party_source"] == "fec-candidate"
    assert "filled" in tags


def test_candidate_does_not_overwrite_existing():
    fix = fixer(cand_full={"jane doe": {"R"}}, cand_initial={})
    rec = {"committee": None, "name": "Jane Doe", "party": "D",
           "party_source": "domain-map", "body": ""}
    fix(rec)
    assert rec["party"] == "D"  # fill-only never overwrites


def test_fill_from_domain_map_retroactive():
    fix = fixer(domain_map={"foo.com": "D"})
    rec = {"committee": None, "name": "News Digest", "party": None,
           "domain": "FOO.com", "body": "", "urls": []}
    tags, old, new = fix(rec)
    assert new == "D" and rec["party_source"] == "domain-map" and "filled" in tags


def test_candidate_fill_idempotent():
    fix = fixer(cand_full={"jane doe": {"D"}}, cand_initial={("doe", "j"): {"D"}})
    rec = {"committee": None, "name": "Jane Doe", "party": None, "body": ""}
    fix(rec)
    tags2, _o, _n = fix(rec)
    assert tags2 - {"source_key_added"} == set()
    assert "source_key_added" not in tags2
    assert rec["party_source"] == "fec-candidate"
