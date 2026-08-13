"""Tests for the committee data-sweep record fixer."""

from apply_committee_fixes import fix_record

DISCLAIMER = "Paid for by NRSC and not authorized by any candidate."


def _rec(**kw):
    base = {"committee": None, "body": ""}
    base.update(kw)
    return base


def test_adds_source_key_and_orders_last():
    rec = {"email": "a@b.com", "committee": "NRSC", "body": DISCLAIMER}
    fix_record(rec)
    assert "committee_source" in rec
    keys = list(rec.keys())
    assert keys[-2:] == ["committee", "committee_source"]


def test_recovers_null_from_disclaimer():
    rec = _rec(committee=None, body=DISCLAIMER)
    tags = fix_record(rec)
    assert rec["committee"] == "NRSC"
    assert rec["committee_source"] == "disclaimer"
    assert "recovered" in tags


def test_infers_disclaimer_when_extract_matches():
    rec = _rec(committee="NRSC", body=DISCLAIMER)
    fix_record(rec)
    assert rec["committee_source"] == "disclaimer"


def test_infers_backfill_when_no_matching_disclaimer():
    rec = _rec(committee="Some Committee", body="no disclaimer here")
    fix_record(rec)
    assert rec["committee_source"] == "backfill"


def test_nulls_garbage_then_recovers_from_disclaimer():
    # garbage stored value, but the body has a valid disclaimer -> recovered
    rec = _rec(committee="recipient@example.com", body=DISCLAIMER)
    tags = fix_record(rec)
    assert rec["committee"] == "NRSC"
    assert rec["committee_source"] == "disclaimer"
    assert "nulled" in tags and "recovered" in tags


def test_idempotent():
    rec = _rec(committee="NRSC", body=DISCLAIMER)
    fix_record(rec)
    tags2 = fix_record(rec)
    assert tags2 == set()  # second pass makes no changes
