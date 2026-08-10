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


from charts import line_chart


def _sample_line():
    dates = ["2026-01-01", "2026-01-02", "2026-02-01"]
    series = {
        "D": [3, 5, 2],
        "R": [1, 0, 4],
        "unknown": [0, 1, 1],
    }
    colors = {"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"}
    return dates, series, colors


def test_line_chart_contains_svg_root():
    dates, series, colors = _sample_line()
    svg = line_chart(dates, series, colors, title="Test")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert 'viewBox="0 0 800 400"' in svg


def test_line_chart_includes_title():
    dates, series, colors = _sample_line()
    svg = line_chart(dates, series, colors, title="My Title")
    assert ">My Title<" in svg


def test_line_chart_has_accessibility_attrs():
    dates, series, colors = _sample_line()
    svg = line_chart(dates, series, colors, title="T")
    assert 'role="img"' in svg
    assert 'aria-label=' in svg


def test_line_chart_draws_a_polyline_per_series():
    dates, series, colors = _sample_line()
    svg = line_chart(dates, series, colors, title="T")
    # One polyline per non-empty series
    assert svg.count("<polyline") == 3


def test_line_chart_uses_series_colors():
    dates, series, colors = _sample_line()
    svg = line_chart(dates, series, colors, title="T")
    assert "#2b6cb0" in svg
    assert "#c53030" in svg
    assert "#a0aec0" in svg


def test_line_chart_has_legend_labels():
    dates, series, colors = _sample_line()
    svg = line_chart(dates, series, colors, title="T")
    assert ">D<" in svg
    assert ">R<" in svg


def test_line_chart_labels_month_boundaries_only():
    dates, series, colors = _sample_line()
    svg = line_chart(dates, series, colors, title="T")
    # First date of each month gets a short month label; mid-month dates don't.
    assert ">Jan 2026<" in svg
    assert ">Feb<" in svg
    assert ">2026-01-02<" not in svg


def test_line_chart_month_labels_show_year_on_first_and_january():
    dates = ["2025-11-15", "2025-12-01", "2026-01-01"]
    svg = line_chart(dates, {"D": [1, 2, 3]}, {"D": "#2b6cb0"}, title="T")
    assert ">Nov 2025<" in svg  # first label carries the year
    assert ">Dec<" in svg  # later months in the same year don't
    assert ">Dec 2025<" not in svg
    assert ">Jan 2026<" in svg  # January restates the year


def test_line_chart_handles_empty_data():
    svg = line_chart([], {}, {}, title="Empty")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


def test_line_chart_handles_single_point():
    svg = line_chart(
        ["2026-01-01"],
        {"D": [5]},
        {"D": "#2b6cb0"},
        title="Single",
    )
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    # A single point still renders a marker/polyline without crashing
