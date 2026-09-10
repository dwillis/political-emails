"""Tests for FEC name matching and the resolve_committee tier ladder."""

import pytest

import fec_match
from fec_match import (
    fec_match_key,
    match_name,
    resolve_committee,
    strip_designators,
)


def _cm(cycle, fid, name, dsgn="", tp="", party="", cand_id=""):
    return (cycle, fid, name, dsgn, tp, party, cand_id)


def _index(cm_rows, cn_rows=()):
    """Build an index like load_fec_index, but from inline rows (no zips)."""
    from fec_match import _cand_committees, build_cand_index, build_index

    index = build_index(cm_rows)
    index["cand_names"] = build_cand_index(cn_rows)
    index["cand_committees"] = _cand_committees(cm_rows)
    return index


def _buckets(index):
    from fec_match import _build_buckets
    return _build_buckets(index)


# ---------------------------------------------------------------------------
# fec_match_key
# ---------------------------------------------------------------------------

def test_fec_match_key_collapses_dotted_initialisms():
    # norm_label splits "U.S." into two letters; the match key re-joins them.
    assert fec_match_key("STEVE GARVEY FOR U.S. SENATE") == (
        fec_match_key("STEVE GARVEY FOR US SENATE")
    )
    assert fec_match_key("D.N.C. Services Corp") == fec_match_key("DNC Services Corp")
    # lone initials are untouched (only adjacent single-letter runs merge)
    assert fec_match_key("John Q Public for Congress") == "john q public for congress"


def test_fec_match_key_ampersand_written_out():
    assert fec_match_key("SMITH & JONES LLC") == fec_match_key("SMITH AND JONES LLC")


def test_fec_match_key_preserves_norm_label_duties():
    from committee_utils import norm_label

    assert norm_label("U.S. Senate") == "u s senate"
    assert fec_match_key("U.S. Senate") == "us senate"


# ---------------------------------------------------------------------------
# match_name (exact/fuzzy report signal)
# ---------------------------------------------------------------------------

def _report_index():
    return _index([
        _cm(2024, "C001", "DSCC"),
        _cm(2024, "C002", "NATIONAL REPUBLICAN SENATORIAL COMMITTEE"),
        _cm(2024, "C003", "WARREN FOR PRESIDENT 16"),
    ])


def test_exact_match():
    idx = _report_index()
    mt, fid, name, score = match_name("dscc", idx, _buckets(idx))
    assert mt == "exact" and fid == "C001" and score == 1.0


def test_no_match():
    idx = _report_index()
    mt, fid, name, score = match_name("Totally Unrelated Group", idx, _buckets(idx))
    assert mt == "none" and fid == "" and score == 0.0


def test_fuzzy_close_match():
    idx = _report_index()
    mt, fid, name, score = match_name("Warren for President 16!", idx, _buckets(idx))
    assert mt in ("exact", "fuzzy") and fid == "C003"


def test_empty_value():
    idx = _report_index()
    assert match_name("", idx, _buckets(idx))[0] == "none"


def test_multi_cycle_exact_key_returns_latest_for_reporting():
    # the load-time multi-cycle bug: one name, two real committees. match_name
    # reports deterministically (latest cycle); resolve_committee picks by cycle.
    idx = _index([
        _cm(2018, "C001", "PRICE FOR CONGRESS"),
        _cm(2024, "C002", "PRICE FOR CONGRESS"),
    ])
    mt, fid, _n, _s = match_name("price for congress", idx, None)
    assert mt == "exact" and fid == "C002"


# ---------------------------------------------------------------------------
# resolve_committee tier ladder
# ---------------------------------------------------------------------------

def _rec(date):
    return {"date": date}


def test_resolve_exact_single_and_multi_cycle():
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    tier, fid, _c = resolve_committee(
        "STEVE GARVEY FOR U.S. SENATE", _rec("2024-09-10T00:00:00+00:00"), idx)
    assert (tier, fid) == ("exact", "C001")


def test_multi_cycle_disambiguation_by_record_cycle():
    idx = _index([
        _cm(2018, "C001", "PRICE FOR CONGRESS", tp="H"),
        _cm(2024, "C002", "PRICE FOR CONGRESS", tp="H"),
    ])
    # 2018 record -> the 2018 committee; 2024 record -> the 2024 committee.
    assert resolve_committee("Price for Congress", _rec("2018-03-01"), idx)[1] == "C001"
    assert resolve_committee("Price for Congress", _rec("2024-03-01"), idx)[1] == "C002"
    # a record between the two registrations is genuinely ambiguous
    tier, fid, cands = resolve_committee("Price for Congress", _rec("2021-03-01"), idx)
    assert tier == "ambiguous" and fid is None and set(cands) == {"C001", "C002"}


def test_strip_unique_designator_variants():
    idx = _index([_cm(2022, "C00772244", "UPSET THE SETUP", tp="O")])
    tier, fid, _c = resolve_committee(
        "Upset The Setup PAC", _rec("2022-08-01"), idx)
    assert (tier, fid) == ("strip-unique", "C00772244")


def test_strip_never_collapses_to_empty():
    idx = _index([_cm(2024, "C001", "AMERICA PAC", tp="O")])
    tier, fid, _c = resolve_committee("PAC", _rec("2024-01-01"), idx)
    assert tier == "none" and fid is None


def test_subset_unique_subset_direction():
    idx = _index([_cm(2016, "C00496075", "RAND PAUL FOR US SENATE", tp="S")])
    tier, fid, _c = resolve_committee(
        "Rand Paul for Senate", _rec("2016-03-01"), idx)
    # the differing token ("us") is generic -> strict -> auto-applied
    assert (tier, fid) == ("subset-unique", "C00496075")


def test_subset_unique_strict_extras_including_years():
    idx = _index([_cm(2024, "C1", "WARNOCK FOR GEORGIA 2024", tp="S")])
    tier, fid, _c = resolve_committee("Warnock for Georgia", _rec("2024-01-01"), idx)
    assert (tier, fid) == ("subset-unique", "C1")


def test_subset_loose_relation_is_review_only():
    # non-generic extras (a person's first name) put the pair in review
    idx = _index([_cm(2022, "C1", "NORTH CAROLINA VICTORY", tp="O")])
    tier, fid, cands = resolve_committee(
        "North Carolina Victory Rally", _rec("2022-10-01"), idx)
    assert tier == "ambiguous" and fid is None and cands == ["C1"]


def test_subset_generic_token_name_never_automatches():
    # the class that failed the spot-check: tiny/generic FEC names must not
    # swallow every query sharing a token
    idx = _index([
        _cm(2024, "C1", "CAMPAIGN COMMITTEE", tp="Q"),
        _cm(2024, "C2", "AMERICAN ACTION NEWS", tp="O"),
        _cm(2024, "C3", "TAYLOR FOR CONGRESS", tp="H"),
    ])
    for query in ("Democratic Legislative Campaign Committee",
                  "The National Redistricting Action Fund",
                  "Marjorie Taylor Greene for Congress"):
        tier, fid, _c = resolve_committee(query, _rec("2024-01-01"), idx)
        assert tier != "subset-unique" and fid is None, query


def test_subset_reordered_tokens_not_automatched():
    # same token set in a different order is a different name, not a variant
    idx = _index([
        _cm(2024, "C1", "AMERICAN PATRIOTS UNITED", tp="O"),
        _cm(2024, "C2", "VIRGINIA FREEDOM PAC", tp="O"),
    ])
    tier, fid, _c = resolve_committee("United American Patriots", _rec("2024-01-01"), idx)
    assert fid is None
    tier, fid, _c = resolve_committee("Freedom Virginia", _rec("2024-01-01"), idx)
    assert fid is None


def test_subset_governor_never_matches_federal():
    # the Spanberger pin: a gubernatorial query never matches the federal
    # committee, even with a unique person and token containment.
    idx = _index([_cm(2020, "C00649913", "SPANBERGER FOR CONGRESS", tp="H",
                      party="DEM", cand_id="H8VA07374")],
                 cn_rows=[("H8VA07374", "SPANBERGER, ABIGAIL", "DEM", "H")])
    tier, fid, _c = resolve_committee(
        "Spanberger for Governor", _rec("2025-06-01"), idx)
    assert tier == "none" and fid is None


def test_subset_office_type_mismatch_rejected():
    idx = _index([_cm(2024, "C1", "GARVEY FOR PRESIDENT", tp="P")])
    tier, fid, _c = resolve_committee(
        "Garvey for Senate", _rec("2024-01-01"), idx)
    assert tier == "none" and fid is None


def test_subset_cycle_guard():
    # a committee registered well after the record's date never matches
    idx = _index([_cm(2024, "C1", "FRESH LEADERSHIP FUND", tp="O")])
    tier, fid, _c = resolve_committee("Fresh Leadership", _rec("2016-01-01"), idx)
    assert tier == "none" and fid is None


def test_save_america_jfc_never_automatches():
    # the plan's verified ambiguous case: colloquial "Save America JFC" must
    # not auto-resolve to any SAVE AMERICA committee; it stays for review.
    # Across cycles too: the two same-named committees registered two cycles
    # apart must never let the cycle window pick one for a variant query.
    idx = _index([
        _cm(2020, "C00762591", "SAVE AMERICA", dsgn="D", tp="Q"),
        _cm(2022, "C00823781", "SAVE AMERICA", dsgn="P", tp="H"),
        _cm(2022, "C00770941", "TRUMP SAVE AMERICA JOINT FUNDRAISING COMMITTEE",
            dsgn="J", tp="N"),
    ])
    for date in ("2018-03-01", "2020-03-01", "2022-03-01", "2024-03-01",
                 "2026-03-01"):
        tier, fid, cands = resolve_committee("Save America JFC", _rec(date), idx)
        assert fid is None, date
        assert tier in ("ambiguous", "none"), date
        if tier == "ambiguous":
            assert "C00770941" not in cands  # different name, no subset relation


def test_acronym_unique():
    idx = _index([_cm(2020, "C1", "VOTER PROTECTION PROJECT", tp="O")])
    tier, fid, _c = resolve_committee("VPP", _rec("2020-01-01"), idx)
    assert (tier, fid) == ("acronym-unique", "C1")


def test_acronym_ambiguous_when_shared():
    idx = _index([
        _cm(2020, "C1", "VOTER PROTECTION PROJECT", tp="O"),
        _cm(2020, "C2", "VERY PRIVATE PAC", tp="O"),
    ])
    tier, fid, _c = resolve_committee("VPP", _rec("2020-01-01"), idx)
    assert tier == "ambiguous" and fid is None


def test_cand_link_via_candidate_master():
    idx = _index(
        [_cm(2024, "C9", "MOULTON FOR CONGRESS", tp="H", party="DEM",
             cand_id="H2MA00633")],
        cn_rows=[("H2MA00633", "MOULTON, SETH", "DEM", "H")])
    tier, fid, _c = resolve_committee(
        "Seth Moulton 2026", _rec("2024-05-01"), idx)
    assert (tier, fid) == ("cand-link", "C9")


def test_cand_link_rejects_party_contradiction():
    idx = _index(
        [_cm(2024, "C9", "MOULTON FOR CONGRESS", tp="H", party="REP",
             cand_id="H2MA00633")],
        cn_rows=[("H2MA00633", "MOULTON, SETH", "DEM", "H")])
    tier, fid, _c = resolve_committee(
        "Republican Seth Moulton", _rec("2024-05-01"), idx)
    assert tier == "none" and fid is None


def test_empty_and_none_values():
    idx = _index([])
    assert resolve_committee("", _rec("2024-01-01"), idx)[0] == "none"
    assert resolve_committee(None, _rec("2024-01-01"), idx)[0] == "none"


def test_strip_designators_tokens():
    assert strip_designators("upset the setup pac") == "upset the setup"
    assert strip_designators("x pac inc") == "x"
    assert strip_designators("democratic victory fund") == "democratic victory"
    assert strip_designators("victory fund rally") == "victory fund rally"