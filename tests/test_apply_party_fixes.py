"""Tests for the party sweep record fixer."""

from apply_party_fixes import make_record_fixer, recent_day_files
from utils import DATA_DIR


def fixer(committee_map=None, domain_map=None, cand_full=None, cand_initial=None,
          fec_party_map=None, blocked=frozenset()):
    return make_record_fixer(committee_map or {}, domain_map or {},
                             cand_full or {}, cand_initial or {}, fec_party_map, blocked)


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


# --- resolved committee_fec_id ---

def test_fec_id_fills_null():
    fix = fixer(fec_party_map={"C001": "R"})
    rec = {"committee": "YOUR AMERICA PAC", "committee_fec_id": "C001",
           "party": None, "body": ""}
    tags, _old, new = fix(rec)
    assert new == "R" and rec["party_source"] == "fec" and "filled" in tags


def test_fec_id_beats_name_exact_match():
    # Name exact-matches a different candidate's D committee; the record's
    # resolved id is an R committee, so no D correction.
    fix = fixer({"casey for congress": ("D", "fec")}, fec_party_map={"C_R": "R", "C_D": "D"})
    rec = {"committee": "Casey for Congress", "committee_fec_id": "C_R",
           "party": "R", "body": ""}
    tags, _old, new = fix(rec)
    assert new == "R" and rec["party_source"] == "fec"
    assert "corrected" not in tags


def test_fec_id_blank_party_drops_name_match():
    # Resolved committee has no FEC party: the name-exact match (another
    # committee) must not label it.
    fix = fixer({"taylor for congress": ("I", "fec")}, fec_party_map={"C_BLANK": None})
    rec = {"committee": "Taylor for Congress", "committee_fec_id": "C_BLANK",
           "party": "R", "body": "", "urls": []}
    _tags, _old, new = fix(rec)
    assert new == "R"


def test_override_beats_fec_id():
    fix = fixer({"save america jfc": ("R", "override")}, fec_party_map={"C1": "D"})
    rec = {"committee": "Save America JFC", "committee_fec_id": "C1", "party": None, "body": ""}
    fix(rec)
    assert rec["party"] == "R" and rec["party_source"] == "override"


def test_fec_id_idempotent():
    fix = fixer(fec_party_map={"C001": "R"})
    rec = {"committee": "X PAC", "committee_fec_id": "C001", "party": None, "body": ""}
    fix(rec)
    tags2, _o, _n = fix(rec)
    assert tags2 == set()


def test_recent_day_files():
    files = [DATA_DIR / "2026" / "10" / f"2026-10-{d:02d}.jsonl" for d in (1, 14, 15)]
    files.append(DATA_DIR / "undated.jsonl")
    assert recent_day_files(files, "2026-10-14") == files[1:3]


def test_fec_id_does_not_overwrite_curated_domain_label():
    # Registry linked the wrong committee; the domain-map label stands.
    fix = fixer(domain_map={"elizabethwarren.com": "D"}, fec_party_map={"C_WRONG": "R"})
    rec = {"committee": "Warren for President", "committee_fec_id": "C_WRONG",
           "party": "D", "party_source": "domain-map", "domain": "elizabethwarren.com",
           "body": "", "urls": []}
    tags, _old, new = fix(rec)
    assert new == "D" and rec["party_source"] == "domain-map" and tags == set()


def test_unresolved_fec_id_ignores_name_exact_match():
    # Backfill visited the record but the registry left it unresolved (None):
    # the exact-name FEC match (another McCarthy) must not flip the label.
    fix = fixer({"mccarthy for congress": ("D", "fec")})
    rec = {"committee": "McCarthy for Congress", "committee_fec_id": None,
           "party": "R", "party_source": "fec", "body": "", "urls": []}
    tags, _old, new = fix(rec)
    assert new == "R" and "corrected" not in tags


def test_none_override_blocks_fec_id():
    fix = fixer(fec_party_map={"C1": "R"}, blocked={"some org"})
    rec = {"committee": "Some Org", "committee_fec_id": "C1", "party": None, "body": ""}
    fix(rec)
    assert rec["party"] is None


def test_fec_id_does_not_overwrite_candidate_supported_label():
    # After a sweep retags the label fec-candidate, the wrong id still loses.
    fix = fixer(cand_full={"elizabeth warren": {"D"}}, fec_party_map={"C_WRONG": "R"})
    rec = {"committee": "Warren for President", "name": "Elizabeth Warren",
           "committee_fec_id": "C_WRONG", "party": "D", "party_source": "fec-candidate",
           "body": "", "urls": []}
    _tags, _old, new = fix(rec)
    assert new == "D" and rec["party_source"] == "fec-candidate"
