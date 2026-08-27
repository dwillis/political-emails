"""Tests for party-review collection in build_review_site."""

from build_review_site import collect_party


def _rec(committee, party, date, body=""):
    return {
        "committee": committee, "party": party, "date": date,
        "name": "Sender", "email": "s@x.org", "domain": "x.org",
        "subject": "Subj", "body": body,
    }


def test_collect_party_groups_and_filters():
    records = [
        _rec("Acme for Congress", None, "2026-01-01"),
        _rec("Acme for Congress", None, "2026-03-01"),   # most recent rep
        _rec("Acme for Congress", "R", "2026-02-01"),    # has party -> ignored
        _rec("Beta PAC", None, "2026-01-05"),
        _rec("", None, "2026-01-05"),                     # no committee -> ignored
    ]
    out = collect_party(records, min_records=1)

    # sorted by party-null count desc
    assert [r["committee"] for r in out] == ["Acme for Congress", "Beta PAC"]
    acme = out[0]
    assert acme["count"] == 2
    assert acme["date"] == "2026-03-01"            # representative = most recent


def test_collect_party_respects_min_records():
    records = [
        _rec("Big Cmte", None, "2026-01-01"),
        _rec("Big Cmte", None, "2026-01-02"),
        _rec("Small Cmte", None, "2026-01-01"),
    ]
    out = collect_party(records, min_records=2)
    assert [r["committee"] for r in out] == ["Big Cmte"]
