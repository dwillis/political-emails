"""Tests for the weekly mention-trend analysis."""

from mention_trend import (
    add_record,
    build_payload,
    last_complete_week,
    mention_text,
    new_tracker,
    summarize,
    two_proportion_p,
    wilson,
    windows,
)

PEOPLE = {"donald_trump": {"name": "Donald Trump",
                           "patterns": [r"\bDonald\s+Trump\b", r"\bPresident\s+Trump\b", r"\bTrump\b"]}}


def rec(committee, body="", subject="", party="R", disclaimer=True, **extra):
    return {"committee": committee, "party": party, "disclaimer": disclaimer,
            "subject": subject, "clean_body": body, **extra}


def test_mention_text_drops_disclaimer():
    r = rec("X", body="Hello. Paid for by Trump National Committee JFC")
    assert "Trump" not in mention_text(r)


def test_disclaimer_only_mention_is_not_counted_and_subject_is():
    t = new_tracker(PEOPLE)
    add_record(t, rec("A PAC", body="Donate now. Paid for by Trump Victory"), "2026-10-05")
    add_record(t, rec("A PAC", subject="Trump needs you", body="Donate now"), "2026-10-05")
    b = t["tallies"]["donald_trump"][("2026-10-05", "R", "a pac")]
    assert b[:3] == [2, 1, 1]


def test_overlapping_patterns_count_once_per_occurrence():
    t = new_tracker(PEOPLE)
    add_record(t, rec("A PAC", body="Donald Trump and President Trump and Trump"), "2026-10-05")
    b = t["tallies"]["donald_trump"][("2026-10-05", "R", "a pac")]
    assert b[3] == 3
    assert b[4] == 7  # words


def test_skips_non_disclaimer_unknown_party_and_no_committee():
    t = new_tracker(PEOPLE)
    add_record(t, rec("A PAC", body="Trump", disclaimer=False), "2026-10-05")
    add_record(t, rec("A PAC", body="Trump", party=None), "2026-10-05")
    add_record(t, rec(None, body="Trump"), "2026-10-05")
    assert not t["tallies"]["donald_trump"]


def test_last_complete_week_and_windows():
    assert last_complete_week("2026-11-01") == "2026-10-26"  # Sunday: week complete
    assert last_complete_week("2026-10-31") == "2026-10-19"  # Saturday: partial
    base, recent = windows("2026-10-26", 2, baseline_weeks=3)
    assert recent == ["2026-10-19", "2026-10-26"]
    assert base == ["2026-09-28", "2026-10-05", "2026-10-12"]


def test_wilson_and_p_value():
    lo, hi = wilson(30, 100)
    assert lo < 30 < hi
    assert wilson(0, 0) == (None, None)
    assert two_proportion_p(40, 100, 20, 100) < 0.01
    assert two_proportion_p(20, 100, 20, 100) == 1.0


def _scenario():
    """Baseline weeks: Steady PAC 10/10 mention, Fader PAC 10/10.
    Recent weeks: Steady 10/10, Fader 2/10, New PAC 0/10, Trump JFC 10/10."""
    t = new_tracker(PEOPLE)
    for i in range(10):
        add_record(t, rec("Steady PAC", body="Trump"), "2026-10-05")
        add_record(t, rec("Fader PAC", body="Trump"), "2026-10-05")
        add_record(t, rec("Steady PAC", body="Trump"), "2026-10-26")
        add_record(t, rec("Fader PAC", body="Trump" if i < 2 else "Taxes"), "2026-10-26")
        add_record(t, rec("New PAC", body="Taxes"), "2026-10-26")
        add_record(t, rec("Trump National Committee JFC", body="Trump"), "2026-10-26")
    return t


def test_summarize_panel_and_decomposition():
    s = summarize(_scenario(), "donald_trump", "R", "2026-10-26", 1,
                  baseline_weeks=3, min_emails=10, person_name="Donald Trump")
    all_row = s["rows"][0]
    assert all_row["baseline"]["share"] == 100.0
    assert all_row["recent"]["emails"] == 40 and all_row["recent"]["share"] == 55.0
    ex = s["rows"][1]
    assert ex["label"].endswith("Donald Trump")
    assert ex["recent"]["emails"] == 30  # JFC excluded

    d = s["decomposition"]
    assert d["continuing"]["committees"] == 2
    assert d["new"]["committees"] == 2  # New PAC + JFC
    assert d["exited"]["committees"] == 0
    total = d["continuing"]["recent"]["emails"] + d["new"]["recent"]["emails"]
    assert total == all_row["recent"]["emails"]  # decomposition closes
    assert d["continuing"]["recent"]["share"] == 60.0

    assert [p["committee"] for p in s["panel"]] == ["Fader PAC", "Steady PAC"]
    assert s["panel"][0]["change_pts"] == -80.0
    assert s["panel_counts"] == {"fell": 1, "flat": 1, "rose": 0}


def test_panel_min_emails_filter():
    s = summarize(_scenario(), "donald_trump", "R", "2026-10-26", 1,
                  baseline_weeks=3, min_emails=11)
    assert s["panel"] == []


def test_build_payload_shape():
    p = build_payload(_scenario(), PEOPLE, "2026-11-01", recent_windows=(1,), baseline_weeks=3)
    person = p["people"]["donald_trump"]
    assert p["last_complete_week"] == "2026-10-26"
    assert set(person["summaries"]) == {"R", "D"}
    weekly = person["weekly"]["R"]
    assert [w[0] for w in weekly] == ["2026-10-05", "2026-10-26"]
    # week, emails, mentions, ..., committees, affiliated_emails, affiliated_mentions
    assert weekly[1][1] == 40 and weekly[1][6] == 4 and weekly[1][7] == 10
