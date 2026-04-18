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
