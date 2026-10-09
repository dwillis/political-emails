"""Tests for build_site stat computation."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_site import _clean_preview, compute_sender_mentions, compute_stats, mention_text


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_clean_preview_strips_table_debris():
    text = "You are the LAST holdout from 34223 | | | | | Are you still there"
    out = _clean_preview(text)
    assert "|" not in out
    assert "holdout from 34223 Are you still there" in out


def test_clean_preview_strips_markdown_and_collapses():
    assert _clean_preview("## Header --- **bold** text") == "Header bold text"


def test_clean_preview_truncates_on_word_boundary():
    out = _clean_preview("word " * 100, limit=40)
    assert out.endswith("…") and len(out) <= 42 and " wor" not in out[-5:]


def test_build_recent_includes_committee(tmp_path, monkeypatch):
    import build_site
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=30)).isoformat()
    d = now.date()
    path = tmp_path / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.year:04d}-{d.month:02d}-{d.day:02d}.jsonl"
    _write_jsonl(path, [{
        "date": recent, "name": "Team X", "email": "a@x.com", "domain": "x.com",
        "subject": "Hi", "body": "hello | | |", "committee": "X for Congress",
        "party": "D", "disclaimer": True,
    }])
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_site, "DOCS_DIR", tmp_path / "docs")
    build_site.build_recent()
    payload = json.loads((tmp_path / "docs" / "recent.json").read_text())
    assert payload["emails"][0]["committee"] == "X for Congress"
    assert "|" not in payload["emails"][0]["preview"]


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


def test_mention_text_excludes_disclaimer_and_footer_but_keeps_subject():
    rec = {
        "subject": "President Trump update",
        "clean_body": "Campaign copy. Paid for by Friends of Donald Trump. Unsubscribe",
    }
    assert mention_text(rec) == "President Trump update Campaign copy. "


def test_compute_sender_mentions_uses_disclaimer_committee_and_week(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-06.jsonl", [
        {"committee": "Committee A", "disclaimer": True, "party": "D",
         "subject": "Donald J. Trump", "clean_body": "Trump Trump"},
        {"committee": "Committee A", "disclaimer": True, "party": "D",
         "subject": "No name", "clean_body": "Paid for by Donald Trump. Unsubscribe"},
        {"committee": "Committee B", "disclaimer": False, "party": "R",
         "subject": "Trump", "clean_body": "Trump"},
        {"committee": "", "disclaimer": True, "party": "R",
         "subject": "Trump", "clean_body": "Trump"},
    ])
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)
    people = {"donald_trump": {"name": "Donald Trump", "patterns": [r"\bTrump\b"]}}

    result = compute_sender_mentions(build_site.scan_data(), people)

    assert result["people"]["donald_trump"]["name"] == "Donald Trump"
    assert result["people"]["donald_trump"]["weekly"] == [{
        "week": "2026-01-05", "committee": "Committee A", "party": "D",
        "total_emails": 2, "matching_emails": 1,
    }]


def test_compute_sender_mentions_merges_committee_name_variants(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-06.jsonl", [
        {"committee": "Trump National Committee JFC, Inc.", "disclaimer": True,
         "party": "R", "subject": "Trump", "clean_body": ""},
        {"committee": "Trump National Committee JFC, Inc.", "disclaimer": True,
         "party": "R", "subject": "Trump", "clean_body": ""},
        {"committee": "trump national committee jfc, inc", "disclaimer": True,
         "party": "R", "subject": "nothing", "clean_body": ""},
    ])
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)
    people = {"trump": {"name": "Donald Trump", "patterns": [r"\bTrump\b"]}}

    weekly = compute_sender_mentions(build_site.scan_data(), people)["people"]["trump"]["weekly"]

    # All three spellings collapse into one committee row.
    assert len(weekly) == 1
    row = weekly[0]
    assert row["total_emails"] == 3
    assert row["matching_emails"] == 2
    # Display uses the most common raw spelling (the "Inc." variant, 2 of 3).
    assert row["committee"] == "Trump National Committee JFC, Inc."


def test_compute_sender_mentions_groups_by_fec_id_across_string_variants(tmp_path, monkeypatch):
    # These two spellings differ by a comma, so committee_key alone keeps them
    # apart; a shared committee_fec_id must still merge them into one entity.
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-06.jsonl", [
        {"committee": "Trump National Committee JFC, Inc.", "committee_fec_id": "C00873893",
         "disclaimer": True, "party": "R", "subject": "Trump", "clean_body": ""},
        {"committee": "Trump National Committee JFC, Inc.", "committee_fec_id": "C00873893",
         "disclaimer": True, "party": "R", "subject": "Trump", "clean_body": ""},
        {"committee": "Trump National Committee JFC Inc", "committee_fec_id": "C00873893",
         "disclaimer": True, "party": "R", "subject": "nothing", "clean_body": ""},
    ])
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)
    people = {"trump": {"name": "Donald Trump", "patterns": [r"\bTrump\b"]}}

    weekly = compute_sender_mentions(build_site.scan_data(), people)["people"]["trump"]["weekly"]

    # All three collapse under the shared FEC ID despite the comma difference.
    assert len(weekly) == 1
    assert weekly[0]["total_emails"] == 3
    assert weekly[0]["matching_emails"] == 2
    # Display uses the most common raw spelling behind that ID.
    assert weekly[0]["committee"] == "Trump National Committee JFC, Inc."


def test_compute_stats_includes_sender_mentions_in_its_single_scan(tmp_path, monkeypatch):
    _write_jsonl(tmp_path / "2026" / "01" / "2026-01-05.jsonl", [{
        "committee": "Committee A", "disclaimer": True, "party": "R",
        "subject": "Trump", "clean_body": "Message copy",
    }])
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)
    people = {"trump": {"name": "Donald Trump", "patterns": [r"\bTrump\b"]}}

    stats = compute_stats(build_site.scan_data(), keyword_patterns={}, tracked_people=people)

    assert stats["sender_mentions"]["people"]["trump"]["weekly"][0]["matching_emails"] == 1


def test_generate_sender_mentions_page_links_data_and_threshold():
    import build_site

    html = build_site.generate_sender_mentions_html("2026-08-25T12:00:00+00:00")

    assert 'fetch(\'sender_mentions.json\')' in html
    assert "Last 52 weeks" in html
    assert "const MIN_EMAILS = 10" in html


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


# --- Committees aggregation and page ------------------------------------

def _committee_stats(tmp_path, monkeypatch, files):
    for rel, records in files.items():
        _write_jsonl(tmp_path / rel, records)
    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)
    return compute_stats(build_site.scan_data(), keyword_patterns={}, tracked_people={})["committees"]


def test_committees_group_by_fec_id_and_count_non_disclaimer_emails(tmp_path, monkeypatch):
    result = _committee_stats(tmp_path, monkeypatch, {
        "2026/01/2026-01-06.jsonl": [
            {"committee": "Trump National Committee JFC, Inc.", "committee_fec_id": "C00873893",
             "committee_source": "disclaimer", "disclaimer": True, "party": "R"},
            {"committee": "Trump National Committee JFC, Inc.", "committee_fec_id": "C00873893",
             "committee_source": "backfill", "disclaimer": False, "party": "R"},
            {"committee": "Trump National Committee JFC Inc", "committee_fec_id": "C00873893",
             "committee_source": "llm:qwen3:4b", "disclaimer": False, "party": "R"},
            {"committee": "Local Committee", "party": "D"},
            {"committee": None, "party": "D"},
        ],
    })

    assert len(result["committees"]) == 2
    trump, local = result["committees"]
    assert trump["name"] == "Trump National Committee JFC, Inc."
    assert trump["total"] == 3
    assert trump["fec_id"] == "C00873893"
    assert trump["party"] == "R"
    assert trump["sources"] == {"backfill": 1, "disclaimer": 1, "llm": 1}
    assert local["total"] == 1 and local["fec_id"] is None
    assert local["sources"] == {"other": 1}


def test_committees_track_first_last_seen_and_monthly_buckets(tmp_path, monkeypatch):
    result = _committee_stats(tmp_path, monkeypatch, {
        "2026/01/2026-01-31.jsonl": [{"committee": "Alpha", "party": "D"}],
        "2026/02/2026-02-01.jsonl": [{"committee": "Alpha", "party": "D"},
                                     {"committee": "Alpha", "party": "D"}],
        "2026/02/2026-02-03.jsonl": [{"committee": "Beta", "party": "R"}],
    })

    alpha, beta = result["committees"]
    assert (alpha["name"], alpha["first_seen"], alpha["last_seen"], alpha["total"]) == (
        "Alpha", "2026-01-31", "2026-02-01", 3)
    assert beta["first_seen"] == "2026-02-03"
    assert result["monthly"] == [["2026-01", 0, 1], ["2026-02", 0, 2], ["2026-02", 1, 1]]


def _result_with_totals(totals):
    return {"committees": [
        {"name": f"C{i}", "party": "D", "fec_id": None, "first_seen": f"{2020 + i % 3}-01-01",
         "last_seen": "2026-01-01", "total": t, "sources": {}}
        for i, t in enumerate(totals)], "monthly": []}


def test_committee_concentration_shares_and_half_rank():
    import build_site
    result = _result_with_totals([50, 20, 10, 10, 5, 5])  # total 100

    conc = build_site.committee_concentration(result)

    assert conc["total_committees"] == 6 and conc["total_emails"] == 100
    assert conc["top10_share"] == 1.0
    assert conc["half_rank"] == 1
    assert sum(emails for _, emails in conc["segments"]) == 100
    assert conc["segments"][0][0] == "Top 10 (100%)"


def test_committee_concentration_segments_split_on_rank_boundaries():
    import build_site
    result = _result_with_totals([10] * 10 + [5] * 40 + [1] * 50 + [1] * 100)

    segments = build_site.committee_concentration(result)["segments"]

    assert [e for _, e in segments] == [100, 200, 50, 100]
    assert [l.split(" (")[0] for l, _ in segments] == [
        "Top 10", "Ranks 11–50", "Ranks 51–100", "All others"]


def test_committee_concentration_empty():
    import build_site
    conc = build_site.committee_concentration({"committees": [], "monthly": []})
    assert conc["half_rank"] == 0 and conc["segments"] == []
    assert build_site.build_committee_charts({"committees": [], "monthly": []}) == ""


def test_committees_first_seen_by_year_counts_each_committee_once():
    import build_site
    result = _result_with_totals([3, 3, 3, 3])  # first_seen years 2020,2021,2022,2020

    assert build_site.committees_first_seen_by_year(result) == {"2020": 2, "2021": 1, "2022": 1}


def test_generate_committees_page_links_data_and_controls():
    import build_site

    html = build_site.generate_committees_html("2026-10-02T12:00:00+00:00")

    assert "fetch('committees.json')" in html
    assert "const MIN_EMAILS = 10" in html
    assert "Last 12 months" in html
    assert 'id="party"' in html and 'id="search"' in html
    assert "__" not in html.replace("__proto__", "")


def test_dashboard_links_committees_and_shows_committee_charts():
    import build_site
    stats = {
        "total_records": 100, "disclaimer_count": 50, "unique_domains": 1,
        "party_counts": {"D": 40, "R": 40, "OTH": 10, "unknown": 10},
        "by_year": {"2026": {"total": 100, "D": 40, "R": 40, "OTH": 10, "unknown": 10, "disclaimer": 50}},
        "top_domains": [("a.com", 5)], "keyword_daily": {}, "all_dates": [],
        "committees": _result_with_totals([50, 30, 20]),
    }
    recent = {"count": 0, "end_iso": "2026-10-02T00:00:00+00:00", "emails": []}

    html = build_site.generate_dashboard_html(stats, {}, recent)

    assert 'href="committees.html"' in html
    assert "Share of email by committee rank" in html
    assert "New committees per year" in html
    assert "3 committees have sent email" in html


def test_generate_domains_page_shell_and_data_contract():
    import build_site

    html = build_site.generate_domains_html("2026-10-08T12:00:00+00:00")

    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Domains — Political Email Archive</title>" in html
    assert "fetch('committees.json')" in html
    assert "fetch('committee_domains.json')" in html
    assert "Generated 2026-10-08 12:00 UTC." in html
    assert '<a href="committees.html">Committees</a>' in html
    assert "__" not in html  # template placeholders must all be replaced
