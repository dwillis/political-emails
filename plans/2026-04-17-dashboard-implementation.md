# Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `docs/index.html` with a stats-and-charts dashboard
and move the full downloads archive to `docs/downloads.html`.

**Architecture:** A new pure-function module `scripts/charts.py` emits
inline SVG strings for three chart types. `scripts/build_site.py` is
reworked to do a single-pass JSONL scan that produces a rich `Stats`
dict, and to write two HTML files (dashboard + downloads). Color
palette: forest green primary (`#1e4d2b`), amber accent (`#e89b3c`).

**Tech Stack:** Python 3.12 stdlib, `html2text` (existing dep),
`pytest` (new dev dep), `uv` for runs.

**Design doc:** `plans/2026-04-17-dashboard-design.md`

**Working directory:** `/Users/dwillis/code/political-emails`
(commands below assume this as CWD unless stated otherwise).

---

## Task 1: Set up test infrastructure

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`

**Step 1: Update pyproject.toml to add pytest + pythonpath config**

Replace contents of `pyproject.toml` with:

```toml
[project]
name = "political-emails"
version = "0.1.0"
description = "Incremental collection and archive of political fundraising emails"
requires-python = ">=3.12"
dependencies = [
    "html2text>=2024.2.26",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[tool.pytest.ini_options]
pythonpath = ["scripts"]
testpaths = ["tests"]
```

**Step 2: Create empty `tests/__init__.py`**

Empty file.

**Step 3: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures."""
```

(Empty body OK; the file just anchors the tests/ dir.)

**Step 4: Install dev deps**

Run: `uv sync --group dev`
Expected: installs pytest, no errors.

**Step 5: Verify pytest can run**

Run: `uv run pytest --collect-only`
Expected: `no tests ran` (we haven't written any yet) and zero collection errors.

**Step 6: Commit**

```bash
git add pyproject.toml tests/
git commit -m "Add pytest test infrastructure"
```

---

## Task 2: Implement `charts.py` — vertical bar chart

**Files:**
- Create: `scripts/charts.py`
- Create: `tests/test_charts.py`

**Step 1: Write the failing test**

Create `tests/test_charts.py`:

```python
"""Tests for the charts module (static SVG generators)."""

from charts import vertical_bar_chart


def test_vertical_bar_chart_contains_svg_root():
    svg = vertical_bar_chart({"2024": 100, "2025": 50}, title="Test", color="#e89b3c")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert 'viewBox="0 0 800 400"' in svg


def test_vertical_bar_chart_includes_category_labels():
    svg = vertical_bar_chart({"2024": 100, "2025": 50}, title="Test", color="#e89b3c")
    assert ">2024<" in svg
    assert ">2025<" in svg


def test_vertical_bar_chart_includes_value_labels():
    svg = vertical_bar_chart({"2024": 100, "2025": 50}, title="Test", color="#e89b3c")
    # Values should appear (formatted as 100 / 50 or "100" / "50")
    assert "100" in svg
    assert "50" in svg


def test_vertical_bar_chart_includes_title():
    svg = vertical_bar_chart({"2024": 100}, title="My Title", color="#e89b3c")
    assert ">My Title<" in svg


def test_vertical_bar_chart_uses_requested_color():
    svg = vertical_bar_chart({"2024": 100}, title="Test", color="#e89b3c")
    assert "#e89b3c" in svg


def test_vertical_bar_chart_has_accessibility_attrs():
    svg = vertical_bar_chart({"2024": 100}, title="My Title", color="#e89b3c")
    assert 'role="img"' in svg
    assert 'aria-label=' in svg


def test_vertical_bar_chart_handles_empty_data():
    svg = vertical_bar_chart({}, title="Empty", color="#e89b3c")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charts.py -v`
Expected: all tests FAIL with `ModuleNotFoundError: No module named 'charts'`.

**Step 3: Implement `scripts/charts.py`**

```python
"""Pure-function SVG chart generators. No JS, no runtime deps."""

from html import escape


# --- Shared layout constants ---------------------------------------------

_VB_WIDTH = 800
_VB_HEIGHT = 400
_MARGIN_LEFT = 60
_MARGIN_RIGHT = 30
_MARGIN_TOP = 50
_MARGIN_BOTTOM = 50
_AXIS_GRAY = "#999"
_LABEL_DARK = "#2c2c2c"
_TITLE_DARK = "#1e4d2b"


def _nice_max(value):
    """Round up to a pleasant axis max (1, 2, 2.5, 5 * 10^n)."""
    if value <= 0:
        return 1
    import math
    exp = math.floor(math.log10(value))
    base = 10 ** exp
    for m in (1, 2, 2.5, 5, 10):
        candidate = m * base
        if candidate >= value:
            return int(candidate) if candidate == int(candidate) else candidate
    return value


def _format_int(n):
    """Format integer with thousands separators."""
    return f"{int(n):,}"


def _svg_open(aria_label, width=_VB_WIDTH, height=_VB_HEIGHT):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{escape(aria_label)}" '
        f'class="chart">'
    )


# --- Vertical bar chart ---------------------------------------------------

def vertical_bar_chart(data, title, color):
    """Render a vertical bar chart as inline SVG.

    Args:
        data: dict mapping category label (str) to numeric value.
        title: str — chart title drawn at top.
        color: str — CSS color for bars.

    Returns: SVG string.
    """
    categories = list(data.keys())
    values = list(data.values())

    aria = f"{title}. " + ", ".join(f"{k}: {_format_int(v)}" for k, v in data.items())
    parts = [_svg_open(aria)]

    # Title
    parts.append(
        f'<text x="{_VB_WIDTH / 2}" y="24" text-anchor="middle" '
        f'font-size="18" font-weight="700" fill="{_TITLE_DARK}" '
        f'font-family="Libre Baskerville, Georgia, serif">{escape(title)}</text>'
    )

    if not data:
        parts.append(
            f'<text x="{_VB_WIDTH / 2}" y="{_VB_HEIGHT / 2}" '
            f'text-anchor="middle" fill="{_AXIS_GRAY}">No data</text>'
        )
        parts.append("</svg>")
        return "".join(parts)

    plot_w = _VB_WIDTH - _MARGIN_LEFT - _MARGIN_RIGHT
    plot_h = _VB_HEIGHT - _MARGIN_TOP - _MARGIN_BOTTOM
    plot_x = _MARGIN_LEFT
    plot_y = _MARGIN_TOP

    max_val = _nice_max(max(values))
    n = len(categories)
    bar_w = plot_w / n * 0.7
    slot_w = plot_w / n

    # Gridlines + y-axis labels at 0, 25, 50, 75, 100%
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = plot_y + plot_h - plot_h * frac
        parts.append(
            f'<line x1="{plot_x}" y1="{y}" x2="{plot_x + plot_w}" y2="{y}" '
            f'stroke="#eee" stroke-width="1"/>'
        )
        label_val = max_val * frac
        parts.append(
            f'<text x="{plot_x - 8}" y="{y + 4}" text-anchor="end" '
            f'font-size="11" fill="{_AXIS_GRAY}">{_format_int(label_val)}</text>'
        )

    # Bars
    for i, (cat, val) in enumerate(data.items()):
        bar_h = plot_h * (val / max_val) if max_val > 0 else 0
        bx = plot_x + slot_w * i + (slot_w - bar_w) / 2
        by = plot_y + plot_h - bar_h
        parts.append(
            f'<rect x="{bx}" y="{by}" width="{bar_w}" height="{bar_h}" '
            f'fill="{color}"/>'
        )
        # Value label above bar
        parts.append(
            f'<text x="{bx + bar_w / 2}" y="{by - 4}" text-anchor="middle" '
            f'font-size="11" fill="{_LABEL_DARK}">{_format_int(val)}</text>'
        )
        # Category label below bar
        parts.append(
            f'<text x="{bx + bar_w / 2}" y="{plot_y + plot_h + 18}" '
            f'text-anchor="middle" font-size="12" fill="{_LABEL_DARK}">'
            f'{escape(str(cat))}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_charts.py -v`
Expected: all 7 tests PASS.

**Step 5: Commit**

```bash
git add scripts/charts.py tests/test_charts.py
git commit -m "Add vertical bar chart SVG generator"
```

---

## Task 3: Implement `charts.py` — horizontal bar chart

**Files:**
- Modify: `scripts/charts.py`
- Modify: `tests/test_charts.py`

**Step 1: Write the failing test**

Append to `tests/test_charts.py`:

```python
from charts import horizontal_bar_chart


def test_horizontal_bar_chart_contains_svg_root():
    svg = horizontal_bar_chart(
        [("actblue.com", 100), ("winred.com", 50)],
        title="Test",
        color="#e89b3c",
    )
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


def test_horizontal_bar_chart_includes_labels_and_values():
    svg = horizontal_bar_chart(
        [("actblue.com", 100), ("winred.com", 50)],
        title="Test",
        color="#e89b3c",
    )
    assert "actblue.com" in svg
    assert "winred.com" in svg
    assert "100" in svg
    assert "50" in svg


def test_horizontal_bar_chart_includes_title():
    svg = horizontal_bar_chart([("foo", 10)], title="My Title", color="#e89b3c")
    assert ">My Title<" in svg


def test_horizontal_bar_chart_has_accessibility_attrs():
    svg = horizontal_bar_chart([("foo", 10)], title="T", color="#e89b3c")
    assert 'role="img"' in svg
    assert 'aria-label=' in svg


def test_horizontal_bar_chart_handles_empty_data():
    svg = horizontal_bar_chart([], title="Empty", color="#e89b3c")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charts.py -v`
Expected: new tests FAIL (ImportError).

**Step 3: Append implementation to `scripts/charts.py`**

```python
# --- Horizontal bar chart -------------------------------------------------

def horizontal_bar_chart(data, title, color):
    """Render a horizontal bar chart.

    Args:
        data: list of (label, value) tuples, already sorted (largest first).
        title: str — chart title.
        color: str — CSS color for bars.

    Returns: SVG string.
    """
    aria = f"{title}. " + ", ".join(f"{k}: {_format_int(v)}" for k, v in data)
    parts = [_svg_open(aria)]

    parts.append(
        f'<text x="{_VB_WIDTH / 2}" y="24" text-anchor="middle" '
        f'font-size="18" font-weight="700" fill="{_TITLE_DARK}" '
        f'font-family="Libre Baskerville, Georgia, serif">{escape(title)}</text>'
    )

    if not data:
        parts.append(
            f'<text x="{_VB_WIDTH / 2}" y="{_VB_HEIGHT / 2}" '
            f'text-anchor="middle" fill="{_AXIS_GRAY}">No data</text>'
        )
        parts.append("</svg>")
        return "".join(parts)

    label_col_w = 180
    value_col_w = 60
    plot_x = _MARGIN_LEFT + label_col_w
    plot_w = _VB_WIDTH - plot_x - _MARGIN_RIGHT - value_col_w
    plot_y = _MARGIN_TOP
    plot_h = _VB_HEIGHT - _MARGIN_TOP - _MARGIN_BOTTOM

    n = len(data)
    bar_h = min(24, (plot_h - (n - 1) * 8) / n) if n > 0 else 24
    slot_h = bar_h + 8

    max_val = max(v for _, v in data)
    max_val = _nice_max(max_val)

    for i, (label, val) in enumerate(data):
        y = plot_y + i * slot_h
        w = plot_w * (val / max_val) if max_val > 0 else 0

        # Label (left-aligned in label column)
        parts.append(
            f'<text x="{plot_x - 10}" y="{y + bar_h / 2 + 4}" '
            f'text-anchor="end" font-size="12" fill="{_LABEL_DARK}">'
            f'{escape(str(label))}</text>'
        )
        # Bar
        parts.append(
            f'<rect x="{plot_x}" y="{y}" width="{w}" height="{bar_h}" '
            f'fill="{color}"/>'
        )
        # Value (right of bar)
        parts.append(
            f'<text x="{plot_x + w + 6}" y="{y + bar_h / 2 + 4}" '
            f'font-size="12" fill="{_LABEL_DARK}">{_format_int(val)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_charts.py -v`
Expected: all tests PASS.

**Step 5: Commit**

```bash
git add scripts/charts.py tests/test_charts.py
git commit -m "Add horizontal bar chart SVG generator"
```

---

## Task 4: Implement `charts.py` — stacked bar chart

**Files:**
- Modify: `scripts/charts.py`
- Modify: `tests/test_charts.py`

**Step 1: Write the failing test**

Append to `tests/test_charts.py`:

```python
from charts import stacked_bar_chart


def _sample_stacked():
    return {
        "2024": {"D": 60, "R": 30, "unknown": 10},
        "2025": {"D": 50, "R": 40, "unknown": 10},
    }


def test_stacked_bar_chart_contains_svg_root():
    svg = stacked_bar_chart(
        _sample_stacked(),
        categories=["D", "R", "unknown"],
        colors={"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"},
        title="Test",
    )
    assert svg.startswith("<svg")


def test_stacked_bar_chart_includes_all_segment_colors():
    svg = stacked_bar_chart(
        _sample_stacked(),
        categories=["D", "R", "unknown"],
        colors={"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"},
        title="Test",
    )
    assert "#2b6cb0" in svg
    assert "#c53030" in svg
    assert "#a0aec0" in svg


def test_stacked_bar_chart_includes_year_labels():
    svg = stacked_bar_chart(
        _sample_stacked(),
        categories=["D", "R", "unknown"],
        colors={"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"},
        title="Test",
    )
    assert ">2024<" in svg
    assert ">2025<" in svg


def test_stacked_bar_chart_has_legend():
    svg = stacked_bar_chart(
        _sample_stacked(),
        categories=["D", "R", "unknown"],
        colors={"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"},
        title="Test",
    )
    # Legend labels appear in addition to within-bar labels
    assert svg.count(">D<") >= 1
    assert svg.count(">R<") >= 1


def test_stacked_bar_chart_handles_empty_data():
    svg = stacked_bar_chart(
        {},
        categories=["D", "R", "unknown"],
        colors={"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"},
        title="Empty",
    )
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_charts.py -v`
Expected: new tests FAIL.

**Step 3: Append implementation to `scripts/charts.py`**

```python
# --- Stacked bar chart ----------------------------------------------------

def stacked_bar_chart(data, categories, colors, title):
    """Render a stacked vertical bar chart.

    Args:
        data: dict { x_label: { category: value, ... } }
        categories: list of category keys in stacking order (bottom -> top).
        colors: dict mapping category -> CSS color.
        title: str — chart title.

    Returns: SVG string.
    """
    aria = f"{title}. " + ", ".join(
        f"{x}: " + "/".join(f"{c}={_format_int(v.get(c, 0))}" for c in categories)
        for x, v in data.items()
    )
    parts = [_svg_open(aria)]

    # Title
    parts.append(
        f'<text x="{_VB_WIDTH / 2}" y="24" text-anchor="middle" '
        f'font-size="18" font-weight="700" fill="{_TITLE_DARK}" '
        f'font-family="Libre Baskerville, Georgia, serif">{escape(title)}</text>'
    )

    # Legend (below title, above plot)
    legend_y = 46
    legend_x = _MARGIN_LEFT
    for cat in categories:
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 10}" width="12" height="12" '
            f'fill="{colors[cat]}"/>'
        )
        parts.append(
            f'<text x="{legend_x + 18}" y="{legend_y}" '
            f'font-size="12" fill="{_LABEL_DARK}">{escape(str(cat))}</text>'
        )
        legend_x += 18 + len(str(cat)) * 8 + 24

    if not data:
        parts.append(
            f'<text x="{_VB_WIDTH / 2}" y="{_VB_HEIGHT / 2}" '
            f'text-anchor="middle" fill="{_AXIS_GRAY}">No data</text>'
        )
        parts.append("</svg>")
        return "".join(parts)

    plot_x = _MARGIN_LEFT
    plot_y = _MARGIN_TOP + 30  # extra space for legend
    plot_w = _VB_WIDTH - _MARGIN_LEFT - _MARGIN_RIGHT
    plot_h = _VB_HEIGHT - plot_y - _MARGIN_BOTTOM

    # Totals per x-value
    totals = {x: sum(v.get(c, 0) for c in categories) for x, v in data.items()}
    max_val = _nice_max(max(totals.values()) if totals else 1)

    n = len(data)
    slot_w = plot_w / n
    bar_w = slot_w * 0.7

    # Gridlines + y-axis labels
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = plot_y + plot_h - plot_h * frac
        parts.append(
            f'<line x1="{plot_x}" y1="{y}" x2="{plot_x + plot_w}" y2="{y}" '
            f'stroke="#eee" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{plot_x - 8}" y="{y + 4}" text-anchor="end" '
            f'font-size="11" fill="{_AXIS_GRAY}">'
            f'{_format_int(max_val * frac)}</text>'
        )

    # Stacks
    for i, (x_label, seg) in enumerate(data.items()):
        total = totals[x_label] or 1  # avoid zero-div for percent labels
        bx = plot_x + slot_w * i + (slot_w - bar_w) / 2

        # Stack from bottom up
        running = 0
        for cat in categories:
            val = seg.get(cat, 0)
            if val <= 0:
                continue
            seg_h = plot_h * (val / max_val) if max_val > 0 else 0
            sy = plot_y + plot_h - plot_h * ((running + val) / max_val)
            parts.append(
                f'<rect x="{bx}" y="{sy}" width="{bar_w}" height="{seg_h}" '
                f'fill="{colors[cat]}"/>'
            )
            # Percent label if segment >= 8% of bar total
            pct = val / total
            if pct >= 0.08 and seg_h >= 16:
                parts.append(
                    f'<text x="{bx + bar_w / 2}" y="{sy + seg_h / 2 + 4}" '
                    f'text-anchor="middle" font-size="10" fill="white">'
                    f'{int(pct * 100)}%</text>'
                )
            running += val

        # x-axis label
        parts.append(
            f'<text x="{bx + bar_w / 2}" y="{plot_y + plot_h + 18}" '
            f'text-anchor="middle" font-size="12" fill="{_LABEL_DARK}">'
            f'{escape(str(x_label))}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_charts.py -v`
Expected: all tests PASS.

**Step 5: Commit**

```bash
git add scripts/charts.py tests/test_charts.py
git commit -m "Add stacked bar chart SVG generator"
```

---

## Task 5: Rewrite stats computation to single-pass rich dict

**Files:**
- Modify: `scripts/build_site.py` (replace `compute_stats()`)
- Create: `tests/test_build_site.py`

**Step 1: Write the failing test**

Create `tests/test_build_site.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_site.py -v`
Expected: FAIL — existing `compute_stats()` returns a tuple, not a dict.

**Step 3: Replace `compute_stats()` in `scripts/build_site.py`**

Replace the existing function body with:

```python
def compute_stats(years):
    """Single-pass scan over JSONL files, return rich stats dict.

    years: output of scan_data().

    Returns dict with:
        total_records, disclaimer_count,
        party_counts: {"D", "R", "unknown"},
        unique_domains: int,
        by_year: { year: {total, D, R, unknown, disclaimer} },
        top_domains: [(domain, count), ...]  sorted desc, top 10.
    """
    from collections import Counter

    total_records = 0
    disclaimer_count = 0
    party_counts = {"D": 0, "R": 0, "unknown": 0}
    domain_counter = Counter()
    by_year = {}

    for year, months in years.items():
        year_stats = {"total": 0, "D": 0, "R": 0, "unknown": 0, "disclaimer": 0}
        for month_num, days in months.items():
            for d in days:
                with open(d["path"]) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        total_records += 1
                        year_stats["total"] += 1

                        party = rec.get("party")
                        bucket = party if party in ("D", "R") else "unknown"
                        party_counts[bucket] += 1
                        year_stats[bucket] += 1

                        if rec.get("disclaimer"):
                            disclaimer_count += 1
                            year_stats["disclaimer"] += 1

                        domain = rec.get("domain")
                        if domain:
                            domain_counter[domain] += 1
        if year_stats["total"] > 0:
            by_year[year] = year_stats

    return {
        "total_records": total_records,
        "disclaimer_count": disclaimer_count,
        "party_counts": party_counts,
        "unique_domains": len(domain_counter),
        "by_year": by_year,
        "top_domains": domain_counter.most_common(10),
    }
```

**Step 4: Update `main()` in `build_site.py` to use the new shape**

Find the existing `main()` call to `compute_stats()` (which currently unpacks
`total_records, unique_domains = compute_stats(years)`) and change it to use
the dict return. The full `main()` will be rewritten in Task 6; for now just
make it compile so the tests can run. Temporary shim at the top of `main()`:

```python
stats = compute_stats(years)
total_records = stats["total_records"]
unique_domains = stats["unique_domains"]
```

Leave the rest of `main()` alone for now — Task 6 rewrites it.

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_site.py -v`
Expected: both tests PASS.

**Step 6: Run the existing site build to confirm no regression**

Run: `uv run python scripts/build_site.py`
Expected: same output as before (scans years, writes index.html, builds
downloads). Site still looks like the old design; that's fine — Task 6
replaces the HTML generation.

**Step 7: Commit**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "Compute rich stats dict in single pass over JSONL"
```

---

## Task 6: Rewrite HTML generation for dashboard + downloads page

**Files:**
- Modify: `scripts/build_site.py`

This is the largest task but has no new logic — only rearranging
existing code into two HTML generators with new styling.

**Step 1: Add imports at top of `build_site.py`**

Near the top, add:

```python
from charts import vertical_bar_chart, stacked_bar_chart, horizontal_bar_chart
```

**Step 2: Add color palette constants**

Near the top (after imports):

```python
# Color palette (forest green + amber)
PRIMARY = "#1e4d2b"       # deep forest green
PRIMARY_LIGHT = "#2d6a3e"  # slightly lighter for header hover/variant
ACCENT = "#e89b3c"         # warm amber
ACCENT_LIGHT = "#f4c37d"   # pale amber
PARTY_COLORS = {"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"}
```

**Step 3: Extract the shared CSS into a constant**

Add after the color constants:

```python
SHARED_CSS = f"""
    :root {{
      --primary: {PRIMARY};
      --primary-light: {PRIMARY_LIGHT};
      --accent: {ACCENT};
      --accent-light: {ACCENT_LIGHT};
      --bg: #fafafa;
      --text: #2c2c2c;
      --border: #ddd;
    }}

    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.6;
    }}

    header {{
      background: var(--primary);
      color: white;
      padding: 2.5rem 1rem 2rem;
      text-align: center;
    }}

    header h1 {{
      font-family: "Libre Baskerville", "Georgia", serif;
      font-size: 2.4rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      margin-bottom: 0.5rem;
    }}

    header h1 span {{ color: var(--accent); }}

    header p {{
      font-size: 1.05rem;
      opacity: 0.85;
      max-width: 600px;
      margin: 0 auto 1rem;
    }}

    .header-links {{
      display: flex; gap: 1.5rem;
      justify-content: center; flex-wrap: wrap;
    }}

    .header-links a {{
      color: var(--accent-light);
      text-decoration: none;
      font-size: 0.9rem;
      border-bottom: 1px solid transparent;
    }}

    .header-links a:hover {{ border-bottom-color: var(--accent-light); }}

    main {{
      max-width: 860px;
      margin: 2rem auto;
      padding: 0 1rem;
    }}

    h2 {{
      font-family: "Libre Baskerville", "Georgia", serif;
      font-size: 1.4rem;
      color: var(--primary);
      margin: 2rem 0 1rem;
      padding-bottom: 0.5rem;
      border-bottom: 2px solid var(--accent);
    }}

    .dl-link {{
      color: var(--primary);
      text-decoration: none;
      font-weight: 500;
      font-size: 0.85rem;
      padding: 0.25rem 0.6rem;
      border: 1px solid var(--primary);
      border-radius: 3px;
    }}
    .dl-link:hover {{ background: var(--primary); color: white; }}
    .dl-zip {{ font-size: 0.95rem; padding: 0.4rem 1rem; }}

    footer {{
      text-align: center;
      padding: 2rem 1rem;
      font-size: 0.8rem;
      color: #999;
      border-top: 1px solid var(--border);
      margin-top: 2rem;
    }}
    footer a {{ color: var(--primary-light); }}
"""
```

**Step 4: Add `generate_dashboard_html()` function**

Add the new function (replaces the old `generate_html`):

```python
def generate_dashboard_html(stats, download_info):
    """Generate the dashboard home page (index.html)."""
    year_range = (
        f"{min(stats['by_year'].keys())}–{max(stats['by_year'].keys())}"
        if stats["by_year"] else "—"
    )

    total = stats["total_records"] or 1  # avoid div-by-zero
    disclaimer_pct = round(100 * stats["disclaimer_count"] / total)
    d_pct = round(100 * stats["party_counts"]["D"] / total)
    r_pct = round(100 * stats["party_counts"]["R"] / total)

    # Prepare chart data
    year_totals = {y: s["total"] for y, s in sorted(stats["by_year"].items())}
    year_party = {
        y: {"D": s["D"], "R": s["R"], "unknown": s["unknown"]}
        for y, s in sorted(stats["by_year"].items())
    }

    chart_emails_per_year = vertical_bar_chart(
        year_totals, title="Emails per year", color=ACCENT
    )
    chart_party_by_year = stacked_bar_chart(
        year_party,
        categories=["D", "R", "unknown"],
        colors=PARTY_COLORS,
        title="Party breakdown by year",
    )
    chart_top_domains = horizontal_bar_chart(
        stats["top_domains"], title="Top 10 sender domains", color=ACCENT
    )

    # Compact downloads: current month + latest full year
    current_month_link = ""
    latest_year_link = ""

    current = download_info.get(CURRENT_YEAR, {})
    if current and current.get("type") == "current":
        months = current.get("months", [])
        if months:
            last = months[-1]  # assume sorted ascending
            current_month_link = (
                f'<div class="dl-row">'
                f'<span class="dl-label">Current month '
                f'({CURRENT_YEAR}-{last["month_num"]}):</span> '
                f'<span class="dl-meta">{last["records"]:,} records '
                f'&middot; {last["zip_size"]}</span> '
                f'<a href="downloads/{last["zip_name"]}" class="dl-link dl-zip">'
                f'Download ZIP</a>'
                f'</div>'
            )

    # Most-recent archive year (not current)
    archive_years = sorted(
        (y for y, info in download_info.items() if info["type"] == "archive"),
        reverse=True,
    )
    if archive_years:
        y = archive_years[0]
        info = download_info[y]
        latest_year_link = (
            f'<div class="dl-row">'
            f'<span class="dl-label">Latest full year ({y}):</span> '
            f'<span class="dl-meta">{info["total_records"]:,} records '
            f'&middot; {info["zip_size"]}</span> '
            f'<a href="downloads/{info["zip_name"]}" class="dl-link dl-zip">'
            f'Download ZIP</a>'
            f'</div>'
        )

    dashboard_css = """
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    .stat-card {
      background: white;
      padding: 1.2rem 1rem;
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      border-radius: 4px;
    }
    .stat-value {
      font-family: "Libre Baskerville", "Georgia", serif;
      font-size: 1.8rem;
      font-weight: 700;
      color: var(--primary);
      line-height: 1.1;
    }
    .stat-label {
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #777;
      margin-top: 0.4rem;
    }
    .stat-meta {
      font-size: 0.8rem;
      color: #888;
      margin-top: 0.3rem;
    }
    .chart {
      width: 100%; height: auto;
      margin-bottom: 2rem;
      background: white;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1rem;
    }
    .dl-row {
      display: flex; align-items: center; gap: 1rem;
      padding: 0.6rem 0.8rem;
      background: white;
      border: 1px solid var(--border);
      border-radius: 4px;
      margin-bottom: 0.5rem;
      flex-wrap: wrap;
    }
    .dl-label { font-weight: 600; color: var(--primary); }
    .dl-meta { color: #888; font-size: 0.9rem; }
    .dl-all { margin-top: 0.6rem; }
    """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Political Email Archive</title>
  <style>{SHARED_CSS}{dashboard_css}</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>An archive of political fundraising emails from {year_range}, with daily updates.</p>
    <div class="header-links">
      <a href="downloads.html">All Downloads</a>
      <a href="https://github.com/dwillis/political-emails">GitHub</a>
    </div>
  </header>

  <main>
    <section class="stats">
      <div class="stat-card">
        <div class="stat-value">{stats["total_records"]:,}</div>
        <div class="stat-label">Total Emails</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{stats["disclaimer_count"]:,}</div>
        <div class="stat-label">With Disclaimer</div>
        <div class="stat-meta">{disclaimer_pct}% of total</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{stats["party_counts"]["D"]:,}</div>
        <div class="stat-label">Democratic</div>
        <div class="stat-meta">{d_pct}% of total</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{stats["party_counts"]["R"]:,}</div>
        <div class="stat-label">Republican</div>
        <div class="stat-meta">{r_pct}% of total</div>
      </div>
    </section>

    {chart_emails_per_year}
    {chart_party_by_year}
    {chart_top_domains}

    <h2>Downloads</h2>
    {current_month_link}
    {latest_year_link}
    <p class="dl-all"><a href="downloads.html">See all monthly and yearly archives →</a></p>
  </main>

  <footer>
    Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>.
    Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.
  </footer>
</body>
</html>"""

    return html
```

**Step 5: Add `generate_downloads_html()` function**

This is the existing `generate_html()` body, renamed and restyled. Add:

```python
def generate_downloads_html(download_info):
    """Generate the full downloads archive page (downloads.html)."""
    if not download_info:
        year_range = "—"
    else:
        year_range = f"{min(download_info.keys())}–{max(download_info.keys())}"

    year_sections = []
    for year in sorted(download_info.keys(), reverse=True):
        info = download_info[year]
        is_current = info["type"] == "current"
        open_attr = " open" if is_current else ""

        if is_current:
            month_rows = []
            for m in info["months"]:
                month_rows.append(
                    f'          <tr>'
                    f'<td>{m["month_name"]}</td>'
                    f'<td class="num">{m["records"]:,}</td>'
                    f'<td class="num">{m["days"]} days</td>'
                    f'<td class="num">{m["zip_size"]}</td>'
                    f'<td><a href="downloads/{m["zip_name"]}" class="dl-link">Download ZIP</a></td>'
                    f'</tr>'
                )
            content = f"""        <table>
          <thead><tr><th>Month</th><th>Records</th><th>Files</th><th>Size</th><th></th></tr></thead>
          <tbody>
{chr(10).join(month_rows)}
          </tbody>
        </table>"""
        else:
            month_list = ", ".join(
                f'{m["month_name"]} ({m["records"]:,})' for m in info["months"]
            )
            content = f"""        <div class="archive-row">
          <a href="downloads/{info['zip_name']}" class="dl-link dl-zip">Download {year}.zip</a>
          <span class="meta">{info['zip_size']} &middot; {info['total_records']:,} records</span>
        </div>
        <p class="months-covered">{month_list}</p>"""

        year_sections.append(f"""      <details{open_attr}>
        <summary>
          <span class="year">{year}</span>
          <span class="year-meta">{info['total_records']:,} records &middot; {len(info['months'])} months</span>
        </summary>
{content}
      </details>""")

    downloads_css = """
    details {
      border: 1px solid var(--border);
      border-radius: 4px;
      margin-bottom: 0.5rem;
      background: white;
    }
    summary {
      padding: 0.8rem 1rem; cursor: pointer;
      display: flex; justify-content: space-between; align-items: center;
      user-select: none;
    }
    summary:hover { background: #f5f5f5; }
    summary::-webkit-details-marker { display: none; }
    summary::before {
      content: "\\25B6"; font-size: 0.7rem; margin-right: 0.7rem;
      color: var(--accent); transition: transform 0.2s;
    }
    details[open] > summary::before { transform: rotate(90deg); }

    .year {
      font-family: "Libre Baskerville", "Georgia", serif;
      font-size: 1.15rem; font-weight: 700; color: var(--primary);
    }
    .year-meta { font-size: 0.85rem; color: #888; }
    details > :not(summary) { padding: 0 1rem 1rem; }

    table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    thead th {
      text-align: left; font-size: 0.75rem;
      text-transform: uppercase; letter-spacing: 0.05em;
      color: #888; padding: 0.5rem 0.5rem;
      border-bottom: 1px solid var(--border);
    }
    td { padding: 0.45rem 0.5rem; border-bottom: 1px solid #eee; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }

    .archive-row {
      display: flex; align-items: center; gap: 1rem;
      margin-bottom: 0.5rem;
    }
    .meta { font-size: 0.85rem; color: #888; }
    .months-covered { font-size: 0.8rem; color: #aaa; }
    """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Downloads — Political Email Archive</title>
  <style>{SHARED_CSS}{downloads_css}</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Downloads</h1>
    <p>All monthly and yearly JSONL archives ({year_range}).</p>
    <div class="header-links">
      <a href="index.html">← Dashboard</a>
      <a href="https://github.com/dwillis/political-emails">GitHub</a>
    </div>
  </header>

  <main>
    <p style="margin-bottom: 1rem; font-size: 0.9rem; color: #666;">
      Data is stored as daily JSONL files (one JSON record per line).
      Current year data is available as monthly ZIP archives;
      past years as yearly ZIPs.
    </p>

{chr(10).join(year_sections)}

  </main>

  <footer>
    Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>.
    Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.
  </footer>
</body>
</html>"""

    return html
```

**Step 6: Delete the old `generate_html()` function**

Remove it entirely. (Tests in `test_build_site.py` only cover
`compute_stats`, so nothing references `generate_html` anymore.)

**Step 7: Rewrite `main()` to produce both pages**

Replace the existing `main()`:

```python
def main():
    print("Building site...")
    years = scan_data()
    print(f"  Scanned {len(years)} years")

    if not years:
        print("  No data found in data/. Writing placeholder index.")
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        (DOCS_DIR / "index.html").write_text(
            "<!doctype html><title>Political Email Archive</title>"
            "<h1>No data yet</h1>"
        )
        return

    stats = compute_stats(years)
    print(f"  Total records: {stats['total_records']:,}")
    print(f"  With disclaimer: {stats['disclaimer_count']:,}")
    print(f"  Unique domains: {stats['unique_domains']:,}")

    download_info = build_downloads(years)
    print(f"  Built downloads for {len(download_info)} years")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    dash_path = DOCS_DIR / "index.html"
    dash_path.write_text(generate_dashboard_html(stats, download_info))
    print(f"  Wrote {dash_path}")

    dl_path = DOCS_DIR / "downloads.html"
    dl_path.write_text(generate_downloads_html(download_info))
    print(f"  Wrote {dl_path}")

    total_downloads = len(list(DOWNLOADS_DIR.iterdir()))
    print(f"  {total_downloads} download files in {DOWNLOADS_DIR}")
    print("Done.")
```

**Step 8: Run tests**

Run: `uv run pytest -v`
Expected: all tests PASS.

**Step 9: Build the site end-to-end**

Run: `uv run python scripts/build_site.py`
Expected output includes both:
- `Wrote /Users/dwillis/code/political-emails/docs/index.html`
- `Wrote /Users/dwillis/code/political-emails/docs/downloads.html`

**Step 10: Eyeball the result**

Open both files in a browser:
```bash
open docs/index.html
open docs/downloads.html
```

Verify:
- Dashboard has 4 stat cards with correct totals
- Three charts render with forest-green/amber palette
- Party-breakdown chart uses blue/red/gray legend
- Compact downloads section links to current month + latest year ZIP
- "All Downloads" / "← Dashboard" links work between pages
- No console errors

**Step 11: Commit**

```bash
git add scripts/build_site.py
git commit -m "Rewrite build_site.py to produce dashboard + downloads pages"
```

---

## Task 7: Final build + commit generated docs

**Step 1: Run a fresh build**

Run: `uv run python scripts/build_site.py`

**Step 2: Inspect the diff**

Run: `git status docs/`
Expected: `docs/index.html`, `docs/downloads.html`, and possibly
`docs/downloads/*.zip` shown as modified/new.

Run: `git diff --stat docs/`
Confirm reasonable file sizes.

**Step 3: Commit generated docs**

```bash
git add docs/
git commit -m "Rebuild docs with new dashboard"
```

**Step 4: Push**

```bash
git push
```

Within a minute, GitHub Pages will serve the new dashboard at
`https://dwillis.github.io/political-emails/`.

**Step 5: Verify live site**

Open `https://dwillis.github.io/political-emails/` in a browser.
Check the same items as Task 6 Step 10 but against the live site.

---

## Verification summary

Run this final command to confirm full test suite passes:

```
uv run pytest -v
```

Expected: all tests pass (charts + build_site). No regressions in
the existing `emails.sh` or `append_emails.py` behaviors (they
aren't touched by this change).
