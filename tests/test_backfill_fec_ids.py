"""Tests for backfill_fec_ids: registry-first, demotion, idempotency, sampling."""

import csv
import random

import backfill_fec_ids as bf
from fec_match import _cand_committees, build_cand_index, build_index


def _cm(cycle, fid, name, dsgn="", tp="", party="", cand_id=""):
    return (cycle, fid, name, dsgn, tp, party, cand_id)


def _index(cm_rows):
    index = build_index(cm_rows)
    index["cand_names"] = {}
    index["cand_committees"] = {}
    return index


def _entity(eid="nfd:acme", fec_id=None, name="Acme", type_="c4", aliases=None):
    return {"id": eid, "fec_id": fec_id, "name": name, "type": type_,
            "party": None, "status": "human",
            "aliases": aliases or [name]}


# ---------------------------------------------------------------------------
# apply_fec_id
# ---------------------------------------------------------------------------

def test_apply_fec_id_sets_exact_match_and_appends_keys_at_end():
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    rec = {"date": "2024-05-01", "email": "a@b.com",
           "committee": "STEVE GARVEY FOR U.S. SENATE"}
    changed, tier, fid, canonical = bf.apply_fec_id(rec, idx, {})
    assert (changed, tier, fid) == (True, "exact", "C001")
    assert rec["committee_fec_id"] == "C001"
    assert canonical is None  # no registry entity for this FEC id
    # the keys are re-inserted at the record's end so diffs stay stable
    assert list(rec)[-2:] == ["committee_fec_id", "committee_canonical"]


def test_apply_fec_id_idempotent():
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    rec = {"date": "2024-05-01", "committee": "STEVE GARVEY FOR U.S. SENATE"}
    bf.apply_fec_id(rec, idx, {})
    changed, _t, _f, _c = bf.apply_fec_id(rec, idx, {})
    assert not changed
    # and from a fresh cache, as the daily recomputation does
    changed, _t, _f, _c = bf.apply_fec_id(rec, idx, {})
    assert not changed


def test_apply_fec_id_idempotent_through_canonical():
    # a canonical change alone (same fid) still marks the record changed
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    rec = {"date": "2024-05-01", "committee": "STEVE GARVEY FOR U.S. SENATE"}
    bf.apply_fec_id(rec, idx, {})
    fec_map = {"C001": {"name": "Garvey Senate"}}
    changed, _t, _f, canonical = bf.apply_fec_id(rec, idx, {}, None, None, fec_map)
    assert changed and canonical == "Garvey Senate"
    changed, _t, _f, _c = bf.apply_fec_id(rec, idx, {}, None, None, fec_map)
    assert not changed


def test_registry_alias_wins_and_skips_the_ladder():
    # the ladder would resolve this name to C001; the committed decision wins
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    registry = {"nfd:acme": _entity(aliases=["STEVE GARVEY FOR U.S. SENATE"])}
    rec = {"date": "2024-05-01", "committee": "STEVE GARVEY FOR U.S. SENATE"}
    changed, tier, fid, canonical = bf.apply_fec_id(rec, idx, {}, registry)
    assert changed and (tier, fid, canonical) == ("registry", None, "Acme")
    # the ladder cache stayed cold (the alias path never touches it)
    assert bf.resolve_fec("STEVE GARVEY FOR U.S. SENATE", None, idx, {})  # sanity
    rec2 = {"date": "2024-05-01", "committee": "STEVE GARVEY FOR U.S. SENATE"}
    cache = {}
    bf.apply_fec_id(rec2, idx, cache, registry)
    assert cache == {}


def test_registry_fec_entity_sets_both_fields():
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    registry = {"C001": _entity("C001", "C001", "Garvey Senate Committee",
                                aliases=["STEVE GARVEY FOR U.S. SENATE"])}
    rec = {"date": "2024-05-01", "committee": "STEVE GARVEY FOR U.S. SENATE"}
    changed, tier, fid, canonical = bf.apply_fec_id(rec, idx, {}, registry)
    assert (tier, fid, canonical) == ("registry", "C001", "Garvey Senate Committee")


def test_registry_noise_entity_canonical_stays_none():
    registry = {"nfd:spam": _entity("nfd:spam", name="Gibberish LLC",
                                    type_="noise")}
    rec = {"date": "2024-05-01", "committee": "Gibberish LLC"}
    changed, tier, fid, canonical = bf.apply_fec_id(rec, None, {}, registry)
    assert (tier, fid, canonical) == ("registry", None, None)


def test_ladder_match_gets_registry_canonical_name():
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    fec_map = {"C001": {"name": "Garvey Senate Committee"}}
    rec = {"date": "2024-05-01", "committee": "STEVE GARVEY FOR U.S. SENATE"}
    changed, tier, fid, canonical = bf.apply_fec_id(rec, idx, {}, None, None, fec_map)
    assert (tier, fid, canonical) == ("exact", "C001", "Garvey Senate Committee")


def test_apply_fec_id_updates_stale_value_and_clears_gone_matches():
    idx = _index([_cm(2024, "C001", "STEVE GARVEY FOR US SENATE", tp="S")])
    rec = {"date": "2024-05-01", "committee": "STEVE GARVEY FOR U.S. SENATE",
           "committee_fec_id": "C999"}
    changed, _t, fid, _c = bf.apply_fec_id(rec, idx, {})
    assert changed and fid == "C001"
    rec = {"date": "2024-05-01", "committee": "Unrelated Group",
           "committee_fec_id": "C999"}
    changed, tier, fid, canonical = bf.apply_fec_id(rec, idx, {})
    assert changed and tier == "none" and fid is None and canonical is None
    assert rec["committee_fec_id"] is None
    assert rec["committee_canonical"] is None


def test_review_only_acronym_tier_never_sets():
    idx = _index([_cm(2020, "C1", "VOTER PROTECTION PROJECT", tp="O")])
    rec = {"date": "2020-01-01", "committee": "VPP"}
    changed, tier, fid, canonical = bf.apply_fec_id(rec, idx, {})
    # the tier resolves for reporting, but the ID is never auto-set; a record
    # that never had the keys reports no change (nothing would be written)
    assert tier == "acronym-unique" and fid is None
    assert rec["committee_fec_id"] is None
    assert rec["committee_canonical"] is None
    stale = {"date": "2020-01-01", "committee": "VPP", "committee_fec_id": "C1"}
    changed, _t, _f, _c = bf.apply_fec_id(stale, idx, {})
    assert changed  # a stale ID does get cleared


def test_review_only_cand_link_tier_never_sets():
    cm = [_cm(2024, "C9", "MOULTON FOR CONGRESS", tp="H", party="DEM",
              cand_id="H2MA00633")]
    idx = build_index(cm)
    idx["cand_names"] = build_cand_index(
        [("H2MA00633", "MOULTON, SETH", "DEM", "H")])
    idx["cand_committees"] = _cand_committees(cm)
    rec = {"date": "2024-05-01", "committee": "Seth Moulton 2026"}
    changed, tier, fid, _c = bf.apply_fec_id(rec, idx, {})
    assert tier == "cand-link" and fid is None and not changed
    assert rec["committee_fec_id"] is None


def test_ambiguous_never_sets():
    idx = _index([
        _cm(2018, "C001", "PRICE FOR CONGRESS"),
        _cm(2024, "C002", "PRICE FOR CONGRESS"),
    ])
    rec = {"date": "2021-05-01", "committee": "Price for Congress"}
    changed, tier, fid, _c = bf.apply_fec_id(rec, idx, {})
    assert tier == "ambiguous" and fid is None
    assert rec["committee_fec_id"] is None
    # a stale ID gets cleared (and that marks the record changed)
    stale = {"date": "2021-05-01", "committee": "Price for Congress",
             "committee_fec_id": "C001"}
    changed, _t, fid, _c = bf.apply_fec_id(stale, idx, {})
    assert changed and fid is None


# ---------------------------------------------------------------------------
# resolve_fec / resolve_for_record
# ---------------------------------------------------------------------------

def test_resolve_fec_memoizes_per_cycle():
    idx = _index([
        _cm(2018, "C001", "PRICE FOR CONGRESS"),
        _cm(2024, "C002", "PRICE FOR CONGRESS"),
    ])
    cache = {}
    t1, f1, _ = bf.resolve_fec("Price for Congress", 2018, idx, cache)
    t2, f2, _ = bf.resolve_fec("Price for Congress", 2024, idx, cache)
    assert len(cache) == 2 and (t1, f1) == ("exact", "C001")
    assert (t2, f2) == ("exact", "C002")
    # a repeated call is served from the cache
    t3, f3, _ = bf.resolve_fec("Price for Congress", 2018, idx, cache)
    assert (t3, f3) == (t1, f1) and len(cache) == 2


def test_resolve_fec_empty_values():
    idx = _index([])
    cache = {}
    for committee in (None, "", "   "):
        assert bf.resolve_fec(committee, 2024, idx, cache) == (None, None, [])
    assert cache == {}


# ---------------------------------------------------------------------------
# spot-check sampling
# ---------------------------------------------------------------------------

_META = {"C1": {"name": "X"}}


def test_spot_check_sample_reservoir_caps_and_keeps_unique_records():
    rng = random.Random(0)
    rows_by_tier, seen_by_tier = {}, {}
    for i in range(250):
        bf.spot_check_sample(rows_by_tier, seen_by_tier, "exact",
                             {"unique_id": f"u{i}", "date": "d", "email": "e"},
                             "COMM", "C1", {"meta": _META}, rng)
    rows = rows_by_tier["exact"]
    assert len(rows) == bf.SAMPLES_PER_TIER
    assert seen_by_tier["exact"] == 250
    assert len({r["unique_id"] for r in rows}) == bf.SAMPLES_PER_TIER
    assert rows[0]["fec_name"] == "X"


def test_write_samples_csv_roundtrip(tmp_path):
    rows_by_tier = {
        "exact": [{"tier": "exact", "unique_id": "u1", "date": "d",
                   "email": "e", "committee": "C", "fec_id": "X",
                   "fec_name": "N"}],
    }
    path = tmp_path / "samples.csv"
    bf.write_samples_csv(path, rows_by_tier, ("exact", "strip-unique"))
    got = list(csv.DictReader(open(path)))
    assert len(got) == 1
    assert got[0]["tier"] == "exact" and got[0]["fec_id"] == "X"
    assert got[0]["fec_name"] == "N"