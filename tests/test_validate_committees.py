"""Tests for validation tiering (owned-domain detection + classify)."""

from collections import Counter

from validate_committees import classify, is_owned

NO_FEC = lambda _c: False  # noqa: E731


def test_is_owned():
    # dominant single committee, enough volume -> owned
    assert is_owned(Counter({"dscc": 90, "other": 10})) is True
    # broker: no single committee dominates -> not owned
    assert is_owned(Counter({"a": 55, "b": 25, "c": 20})) is False
    # too few records -> not owned
    assert is_owned(Counter({"x": 5})) is False


def test_confirmed_by_disclaimer_source():
    rec = {"committee": "NRSC", "committee_source": "disclaimer", "body": "", "domain": "x"}
    assert classify(rec, {}, NO_FEC) == ("CONFIRMED", "disclaimer")


def test_confirmed_by_fec():
    rec = {"committee": "NRSC", "committee_source": "backfill", "body": "", "domain": "x"}
    assert classify(rec, {}, lambda c: True) == ("CONFIRMED", "fec-exact")


def test_contradicts_disclaimer_is_suspect():
    rec = {
        "committee": "Hakeem Jeffries for Congress",
        "committee_source": "backfill",
        "body": "Paid for by Lisa Blunt Rochester for Senate and not authorized",
        "domain": "broker.com",
    }
    assert classify(rec, {}, NO_FEC) == ("SUSPECT", "contradicts-disclaimer")


def test_variant_disclaimer_not_suspect():
    # extract "DSCC" vs stored full name -> same committee, not a contradiction
    dom = {"dscc.org": Counter({"democratic senatorial campaign committee": 50})}
    rec = {
        "committee": "Democratic Senatorial Campaign Committee",
        "committee_source": "backfill",
        "body": "Paid for by DSCC and not authorized by any candidate",
        "domain": "dscc.org",
    }
    tier, _ = classify(rec, dom, NO_FEC)
    assert tier != "SUSPECT"


def test_minority_on_owned_domain_is_suspect():
    dom = {"dscc.org": Counter({"dscc": 990, "democrat for pennsylvania": 5})}
    rec = {
        "committee": "Democrat for Pennsylvania",
        "committee_source": "backfill",
        "body": "no disclaimer",
        "domain": "dscc.org",
    }
    assert classify(rec, dom, NO_FEC) == ("SUSPECT", "minority-on-owned-domain")
