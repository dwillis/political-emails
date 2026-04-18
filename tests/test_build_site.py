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
    ])

    import build_site
    monkeypatch.setattr(build_site, "DATA_DIR", tmp_path)

    stats = compute_stats(build_site.scan_data())

    assert stats["total_records"] == 4
    assert stats["disclaimer_count"] == 2
    assert stats["party_counts"] == {"D": 2, "R": 1, "unknown": 1}
    assert stats["unique_domains"] == 3
    assert stats["by_year"]["2024"]["total"] == 2
    assert stats["by_year"]["2024"]["D"] == 1
    assert stats["by_year"]["2024"]["R"] == 1
    assert stats["by_year"]["2024"]["disclaimer"] == 1
    assert stats["by_year"]["2025"]["total"] == 2
    assert stats["by_year"]["2025"]["unknown"] == 1
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
    assert stats["party_counts"] == {"D": 0, "R": 0, "unknown": 0}
    assert stats["unique_domains"] == 0
    assert stats["by_year"] == {}
    assert stats["top_domains"] == []
