"""Tests for gold-set parsing and join keys."""

from eval_committees import gold_key, parse_gold_date, record_key


def test_parse_gold_date():
    dt = parse_gold_date("11/1/24 0:02")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2024, 11, 1, 0, 2)
    assert parse_gold_date("garbage") is None


def test_gold_key_matches_record_key():
    gold_row = {
        "email": "Info@Example.COM", "subject": "Hi There",
        "year": "2024", "month": "11", "day": "1", "hour": "0", "minute": "2",
    }
    rec = {
        "email": "info@example.com", "subject": "hi there",
        "year": 2024, "month": 11, "day": 1, "hour": 0, "minute": 2,
    }
    assert gold_key(gold_row) == record_key(rec)


def test_gold_key_bad_row():
    assert gold_key({"email": "a", "subject": "b", "year": "notanumber"}) is None
