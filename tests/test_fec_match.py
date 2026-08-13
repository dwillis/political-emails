"""Tests for FEC name matching (no network)."""

from fec_match import match_name


def _index():
    from committee_utils import norm_label
    names = {
        "DSCC": "C001",
        "NATIONAL REPUBLICAN SENATORIAL COMMITTEE": "C002",
        "WARREN FOR PRESIDENT 16": "C003",
    }
    name_index, buckets = {}, {}
    for name, fid in names.items():
        n = norm_label(name)
        name_index[n] = (fid, name, 2024)
        buckets.setdefault((n[0], len(n) // 5), []).append(n)
    return name_index, buckets


def test_exact_match():
    idx, buckets = _index()
    mt, fid, name, score = match_name("dscc", idx, buckets)
    assert mt == "exact" and fid == "C001" and score == 1.0


def test_no_match():
    idx, buckets = _index()
    mt, fid, name, score = match_name("Totally Unrelated Group", idx, buckets)
    assert mt == "none" and fid == "" and score == 0.0


def test_fuzzy_close_match():
    idx, buckets = _index()
    # minor punctuation/casing variant of an indexed name
    mt, fid, name, score = match_name("Warren for President 16!", idx, buckets)
    assert mt in ("exact", "fuzzy") and fid == "C003"


def test_empty_value():
    idx, buckets = _index()
    assert match_name("", idx, buckets)[0] == "none"
