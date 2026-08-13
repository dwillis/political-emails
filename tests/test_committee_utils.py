"""Tests for committee normalization, join keys, and map building."""

from email.message import EmailMessage

import pytest

from committee_utils import (
    build_committee_map,
    join_key,
    needs_committee,
    normalize_committee,
    normalize_date,
    record_key,
)
from process_email import process_single_email


@pytest.mark.parametrize(
    "value",
    ["", "Unknown", " unknown ", "UNKNOWN", "None", "none", "N/A", "n/a", "null", None],
)
def test_normalize_committee_unknowns_become_none(value):
    assert normalize_committee(value) is None


def test_normalize_committee_keeps_real_names_verbatim():
    assert normalize_committee("  NRSC  ") == "NRSC"
    # casing and internal punctuation are preserved (no normalization)
    assert normalize_committee("Trump National Committee JFC, Inc.") == (
        "Trump National Committee JFC, Inc."
    )


def test_normalize_committee_rejects_adapter_leakage():
    assert normalize_committee("Dr. Kim Schrier for Congress [[ ## completed ##") is None
    assert normalize_committee("### **Overview** of the bills...") is None


def test_normalize_committee_rejects_rambling_essays():
    essay = "Based on the text provided, here is a summary of the key information. " * 5
    assert normalize_committee(essay) is None


@pytest.mark.parametrize(
    "value",
    [
        "No committee name found in the email text",
        "I cannot determine the committee",
        "The committee is not specified in the email",
        "Unable to identify the sponsoring committee",
    ],
)
def test_normalize_committee_rejects_abstentions(value):
    assert normalize_committee(value) is None


def test_normalize_committee_keeps_names_with_innocuous_words():
    # real names that must NOT trip the abstention filter
    assert normalize_committee("No Labels") == "No Labels"
    assert normalize_committee("Friends of Cory Booker") == "Friends of Cory Booker"


def test_normalize_date_swaps_only_first_space():
    assert normalize_date("2026-01-18 18:14:58+00:00") == "2026-01-18T18:14:58+00:00"
    # already-ISO input is unchanged
    assert normalize_date("2026-01-18T18:14:58+00:00") == "2026-01-18T18:14:58+00:00"
    assert normalize_date(None) == ""


def test_join_key_matches_across_date_formats_and_casing():
    qwen = join_key("Team@Example.com", "Hello There", "2026-01-18 18:14:58+00:00")
    archive = join_key("team@example.com", "hello there", "2026-01-18T18:14:58+00:00")
    assert qwen == archive


def test_join_key_tolerates_none_date():
    assert join_key("a@b.com", "subj", None) == ("a@b.com", "subj", "")


def test_record_key_from_dict():
    rec = {"email": "A@B.com", "subject": "Hi", "date": "2026-01-18 00:00:00+00:00"}
    assert record_key(rec) == ("a@b.com", "hi", "2026-01-18T00:00:00+00:00")


def _rec(email, subject, date, committee):
    return {"email": email, "subject": subject, "date": date, "committee": committee}


def test_build_map_basic_and_unknown_mapping():
    records = [
        _rec("a@x.com", "s1", "2026-01-01 00:00:00+00:00", "NRSC"),
        _rec("b@x.com", "s2", "2026-01-02 00:00:00+00:00", "Unknown"),
    ]
    mapping, stats = build_committee_map(records)
    assert mapping[("a@x.com", "s1", "2026-01-01T00:00:00+00:00")] == "NRSC"
    assert mapping[("b@x.com", "s2", "2026-01-02T00:00:00+00:00")] is None
    assert stats["parsed"] == 2
    assert stats["mapped_real"] == 1
    assert stats["mapped_null"] == 1
    assert stats["conflicts"] == 0


def test_build_map_real_beats_unknown_regardless_of_order():
    same = ("a@x.com", "s", "2026-01-01 00:00:00+00:00")
    # unknown first, then real
    mapping, _ = build_committee_map(
        [_rec(*same, "Unknown"), _rec(*same, "DSCC")]
    )
    assert mapping[join_key(*same)] == "DSCC"
    # real first, then unknown
    mapping, _ = build_committee_map(
        [_rec(*same, "DSCC"), _rec(*same, "Unknown")]
    )
    assert mapping[join_key(*same)] == "DSCC"


def test_build_map_conflicting_real_values_blank_out():
    same = ("a@x.com", "s", "2026-01-01 00:00:00+00:00")
    mapping, stats = build_committee_map(
        [_rec(*same, "NRSC"), _rec(*same, "DSCC")]
    )
    assert mapping[join_key(*same)] is None
    assert stats["conflicts"] == 1


def test_needs_committee():
    assert needs_committee({"committee": None}) is True
    assert needs_committee({}) is True
    assert needs_committee({"committee": "NRSC"}) is False


def test_process_single_email_includes_committee_none():
    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg["From"] = "sender@example.org"
    msg["Date"] = "Mon, 18 Jan 2026 18:14:58 +0000"
    msg.set_content("hello world")
    record = process_single_email(msg, domain_party_map={})
    assert "committee" in record
    assert record["committee"] is None
