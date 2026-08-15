"""Tests for build_site stat computation."""

import json
from pathlib import Path

from build_site import compute_stats


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_compute_stats_counts_basic(tmp_path, monkeypatch):
    # Two days, two years
    _write_jsonl(tmp_path / "2024" / "01" / "2024-01-15.jsonl", [
        {"domain": "a.com", "party": "D", "disclaimer": True,  "year": 2024},
        {"domain": "b.com", "party": "R", "disclaimer": False, "year": 2024},
    ])
    _write_jsonl(tmp_path / "2025" / "03" / "2025-03-02.jsonl", [
        {"domain": "a.com", "party": "D", "disclaimer": True, "year": 2025},
        {"domain": "c.com", "party": None, "disclaimer": False, "year": 2025},
        {"domain": "d.com", "party": "I", "disclaimer": False, "year": 2025},
        {"domain": "e.com", "party": "G", "disclaimer": False, "year": 2025},
    ])

    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)

    stats = compute_stats(build_site.scan_data())

    assert stats["total_records"] == 6
    assert stats["disclaimer_count"] == 2
    # I and G fold into the OTH (Other) bucket.
    assert stats["party_counts"] == {"D": 2, "R": 1, "OTH": 2, "unknown": 1}
    assert stats["unique_domains"] == 5
    assert stats["by_year"]["2024"]["total"] == 2
    assert stats["by_year"]["2024"]["D"] == 1
    assert stats["by_year"]["2024"]["R"] == 1
    assert stats["by_year"]["2024"]["disclaimer"] == 1
    assert stats["by_year"]["2025"]["total"] == 4
    assert stats["by_year"]["2025"]["unknown"] == 1
    assert stats["by_year"]["2025"]["OTH"] == 2
    # Top domains: a.com=2, then b.com=1, c.com=1 (order of 1-counts not strict)
    top = dict(stats["top_domains"])
    assert top["a.com"] == 2
    assert top["b.com"] == 1
    assert top["c.com"] == 1


def test_compute_stats_empty(tmp_path, monkeypatch):
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)

    stats = compute_stats(build_site.scan_data())
    assert stats["total_records"] == 0
    assert stats["disclaimer_count"] == 0
    assert stats["party_counts"] == {"D": 0, "R": 0, "OTH": 0, "unknown": 0}
    assert stats["unique_domains"] == 0
    assert stats["by_year"] == {}
    assert stats["top_domains"] == []


PATTERNS = {"datacenter": r"\bdata[\s-]?centers?\b"}


def test_daily_span_trailing_window_is_continuous():
    import build_site
    span = build_site._daily_span(
        ["2020-01-01", "2026-06-01", "2026-06-30"], window_days=10
    )
    # 10-day window ending on the last date, one entry per calendar day.
    assert span == [
        "2026-06-21", "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25",
        "2026-06-26", "2026-06-27", "2026-06-28", "2026-06-29", "2026-06-30",
    ]


def test_daily_span_shorter_than_window_uses_full_range():
    import build_site
    span = build_site._daily_span(["2026-06-28", "2026-06-30"], window_days=365)
    assert span == ["2026-06-28", "2026-06-29", "2026-06-30"]


def test_daily_span_empty():
    import build_site
    assert build_site._daily_span([], window_days=365) == []


def test_compute_stats_keyword_daily_tallies_by_party(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-15.jsonl", [
        {"party": "D", "subject": "New data center approved", "body": ""},
        {"party": "R", "subject": "hi", "body": "A datacenter is coming to town"},
        {"party": "R", "subject": "unrelated", "body": "vote today"},
        {"party": None, "subject": "data-centers everywhere", "body": ""},
    ])
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-16.jsonl", [
        {"party": "D", "subject": "no match", "body": "the data is clear"},
    ])

    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)

    stats = compute_stats(build_site.scan_data(), keyword_patterns=PATTERNS)

    daily = stats["keyword_daily"]["datacenter"]
    assert daily["2026-01-15"] == {"D": 1, "R": 1, "OTH": 0, "unknown": 1}
    # Day with no matches is absent (or zeroed); assert no false positives.
    zero = {"D": 0, "R": 0, "OTH": 0, "unknown": 0}
    assert daily.get("2026-01-16", zero) == zero


def test_build_keyword_charts_includes_total_line():
    import build_site

    stats = {
        "keyword_daily": {
            "datacenter": {
                "2026-01-01": {"D": 2, "R": 1},
                "2026-01-02": {"unknown": 3},
            }
        },
        "all_dates": ["2026-01-01", "2026-01-02"],
    }
    html = build_site.build_keyword_charts(stats)
    assert ">Total<" in html
    # Total = D + R + OTH + unknown; 5 polylines (Total + four buckets).
    assert html.count("<polyline") == 5


def test_build_keyword_charts_aggregates_by_week():
    import build_site

    # Two complete Mon-Sun weeks: Jan 5-11 and Jan 12-18, 2026.
    # Weekly sums: D = 2+3 = 5, then 7.
    stats = {
        "keyword_daily": {
            "datacenter": {
                "2026-01-05": {"D": 2},
                "2026-01-07": {"D": 3},
                "2026-01-12": {"D": 7},
            }
        },
        "all_dates": ["2026-01-05", "2026-01-18"],
    }
    html = build_site.build_keyword_charts(stats)
    assert "per week" in html
    # Two weekly points per series: each polyline has exactly two coordinates.
    first_poly = html.split("<polyline")[1]
    points = first_poly.split('points="')[1].split('"')[0]
    assert len(points.split()) == 2


def test_build_keyword_charts_drops_partial_edge_weeks():
    import build_site

    # Span Wed 2026-01-07 .. Tue 2026-01-20: partial leading week (Jan 7-11),
    # one complete week (Jan 12-18), partial trailing week (Jan 19-20).
    # Only the complete week is plotted, so no fake cliff at the edges.
    stats = {
        "keyword_daily": {
            "datacenter": {
                "2026-01-07": {"D": 9},
                "2026-01-14": {"D": 4},
                "2026-01-20": {"D": 9},
            }
        },
        "all_dates": ["2026-01-07", "2026-01-20"],
    }
    html = build_site.build_keyword_charts(stats)
    first_poly = html.split("<polyline")[1]
    points = first_poly.split('points="')[1].split('"')[0]
    assert len(points.split()) == 1


def test_compute_stats_reports_all_dates_sorted(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-16.jsonl", [
        {"party": "D", "subject": "x", "body": ""},
    ])
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-15.jsonl", [
        {"party": "R", "subject": "y", "body": ""},
    ])
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)

    stats = compute_stats(build_site.scan_data(), keyword_patterns=PATTERNS)
    assert stats["all_dates"] == ["2026-01-15", "2026-01-16"]


def test_compute_stats_keyword_counts_email_once_per_day(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "2026" / "02" / "2026-02-01.jsonl", [
        {"party": "D", "subject": "data center data center",
         "body": "datacenter datacenter datacenter"},
    ])
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)

    stats = compute_stats(build_site.scan_data(), keyword_patterns=PATTERNS)
    assert stats["keyword_daily"]["datacenter"]["2026-02-01"]["D"] == 1


def test_compute_stats_keyword_matching_is_word_bounded(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "2026" / "03" / "2026-03-01.jsonl", [
        {"party": "D", "subject": "predatacenterish", "body": ""},
        {"party": "R", "subject": "", "body": "the datacentre in London"},  # British spelling: no match
    ])
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)

    stats = compute_stats(build_site.scan_data(), keyword_patterns=PATTERNS)
    assert "2026-03-01" not in stats["keyword_daily"]["datacenter"] or \
        stats["keyword_daily"]["datacenter"]["2026-03-01"] == {"D": 0, "R": 0, "unknown": 0}


from build_site import generate_topic_html


def _email(uid="u1", date="2026-08-05T09:30:00+00:00", name="Cool PAC",
           email_addr="info@coolpac.org", party="D", subject="Data center news",
           image="2026-08-05_u1.png"):
    return {
        "unique_id": uid, "date": date, "name": name, "email": email_addr,
        "party": party, "subject": subject, "image": image,
    }


def test_generate_topic_html_includes_topic_and_metadata():
    html = generate_topic_html(
        "datacenter", "2026-08-05", [_email()], "2026-08-09T11:30:00Z"
    )
    assert html.startswith("<!DOCTYPE html>")
    assert "datacenter" in html
    assert "Cool PAC" in html
    assert "info@coolpac.org" in html
    assert "2026-08-05" in html


def test_generate_topic_html_renders_image_per_email():
    emails = [_email(uid="u1", image="a.png"), _email(uid="u2", image="b.png")]
    html = generate_topic_html("datacenter", "2026-08-05", emails, "2026-08-09T11:30:00Z")
    assert html.count("<img") == 2
    assert 'src="a.png"' in html
    assert 'src="b.png"' in html


def test_generate_topic_html_intro_wording_and_long_date():
    html = generate_topic_html(
        "datacenter", "2026-08-08", [_email()], "2026-08-09T11:30:00Z"
    )
    assert 'Recent emails mentioning "datacenter" with a campaign disclaimer, ' \
           'from August 8, 2026.' in html
    assert "Up to 3" not in html


def test_generate_topic_html_shows_party_label():
    html = generate_topic_html("datacenter", "2026-08-05", [_email(party="R")],
                               "2026-08-09T11:30:00Z")
    assert ">R<" in html


def test_generate_topic_html_empty_state():
    html = generate_topic_html("datacenter", None, [], "2026-08-09T11:30:00Z")
    assert html.startswith("<!DOCTYPE html>")
    assert "datacenter" in html
    assert "<img" not in html
