"""Tests for FEC name matching (no network)."""

from fec_match import fec_match_key, match_name


def _index():
    from committee_utils import norm_label

    from fec_match import fec_match_key
    names = {
        "DSCC": "C001",
        "NATIONAL REPUBLICAN SENATORIAL COMMITTEE": "C002",
        "WARREN FOR PRESIDENT 16": "C003",
        "STEVE GARVEY FOR US SENATE": "C004",
    }
    name_index, buckets = {}, {}
    for name, fid in names.items():
        k = fec_match_key(name)
        name_index[k] = (fid, name, 2024)
        buckets.setdefault((k[0], len(k) // 5), []).append(k)
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


def test_fec_match_key_matches_garvey_variants():
    idx, buckets = _index()
    mt, fid, name, _score = match_name("STEVE GARVEY FOR U.S. SENATE", idx, buckets)
    assert mt == "exact" and fid == "C004"


def test_fec_match_key_preserves_norm_label_duties():
    # the plain normalizer is deliberately NOT mutated (it keys the party CSV)
    from committee_utils import norm_label

    assert norm_label("U.S. Senate") == "u s senate"
    assert fec_match_key("U.S. Senate") == "us senate"
