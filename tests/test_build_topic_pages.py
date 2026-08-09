"""Tests for topic-page target selection."""

import re

from build_topic_pages import pick_latest_day_targets

PATTERN = re.compile(r"\bdata[\s-]?centers?\b", re.IGNORECASE)


def _rec(uid, party, date, disclaimer=True, subject="data center", body=""):
    return {
        "unique_id": uid,
        "message_id": f"<{uid}>",
        "party": party,
        "date": date,
        "disclaimer": disclaimer,
        "subject": subject,
        "body": body,
    }


def test_picks_most_recent_day_with_matches():
    recs = [
        _rec("a", "D", "2026-08-01T09:00:00+00:00"),
        _rec("b", "R", "2026-08-05T09:00:00+00:00"),
        _rec("c", "D", "2026-08-05T18:00:00+00:00"),
    ]
    day, hits = pick_latest_day_targets(recs, PATTERN, limit=3)
    assert day == "2026-08-05"
    # Newest first, only that day's records
    assert [r["unique_id"] for r in hits] == ["c", "b"]


def test_caps_at_limit_within_the_day():
    recs = [
        _rec("a", "D", "2026-08-05T07:00:00+00:00"),
        _rec("b", "R", "2026-08-05T08:00:00+00:00"),
        _rec("c", "D", "2026-08-05T09:00:00+00:00"),
        _rec("d", "D", "2026-08-05T10:00:00+00:00"),
    ]
    day, hits = pick_latest_day_targets(recs, PATTERN, limit=3)
    assert day == "2026-08-05"
    assert [r["unique_id"] for r in hits] == ["d", "c", "b"]


def test_requires_disclaimer():
    recs = [
        _rec("a", "D", "2026-08-05T09:00:00+00:00", disclaimer=False),
        _rec("b", "R", "2026-08-01T09:00:00+00:00", disclaimer=True),
    ]
    day, hits = pick_latest_day_targets(recs, PATTERN, limit=3)
    assert day == "2026-08-01"
    assert [r["unique_id"] for r in hits] == ["b"]


def test_no_matches_returns_none():
    recs = [_rec("a", "D", "2026-08-05T09:00:00+00:00", subject="unrelated", body="vote")]
    assert pick_latest_day_targets(recs, PATTERN, limit=3) == (None, [])
