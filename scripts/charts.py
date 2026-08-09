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


# --- Line chart -----------------------------------------------------------

def line_chart(dates, series, colors, title):
    """Render a multi-series line chart as inline SVG.

    Args:
        dates: ordered list of x-axis labels (e.g. "YYYY-MM-DD" strings).
        series: dict { series_label: [values aligned to dates] }.
        colors: dict mapping series_label -> CSS color.
        title: str — chart title.

    Returns: SVG string. X-axis labels are drawn only at month boundaries
    (the first date of each distinct YYYY-MM) to avoid crowding.
    """
    aria = f"{title}. " + ", ".join(
        f"{label}: {_format_int(sum(vals))}" for label, vals in series.items()
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
    for label in series:
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 10}" width="12" height="12" '
            f'fill="{colors.get(label, _AXIS_GRAY)}"/>'
        )
        parts.append(
            f'<text x="{legend_x + 18}" y="{legend_y}" '
            f'font-size="12" fill="{_LABEL_DARK}">{escape(str(label))}</text>'
        )
        legend_x += 18 + len(str(label)) * 8 + 24

    if not dates or not series:
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

    all_values = [v for vals in series.values() for v in vals]
    max_val = _nice_max(max(all_values) if all_values else 1)

    n = len(dates)
    # X position for each date index. Single point sits at the left edge.
    def x_at(i):
        return plot_x if n == 1 else plot_x + plot_w * (i / (n - 1))

    def y_at(val):
        return plot_y + plot_h - plot_h * (val / max_val if max_val > 0 else 0)

    # Gridlines + y-axis labels
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = plot_y + plot_h - plot_h * frac
        parts.append(
            f'<line x1="{plot_x}" y1="{y}" x2="{plot_x + plot_w}" y2="{y}" '
            f'stroke="#eee" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{plot_x - 8}" y="{y + 4}" text-anchor="end" '
            f'font-size="11" fill="{_AXIS_GRAY}">{_format_int(max_val * frac)}</text>'
        )

    # X-axis labels at month boundaries only (first occurrence of each YYYY-MM)
    seen_months = set()
    for i, d in enumerate(dates):
        month_key = str(d)[:7]  # "YYYY-MM"
        if month_key in seen_months:
            continue
        seen_months.add(month_key)
        x = x_at(i)
        parts.append(
            f'<line x1="{x}" y1="{plot_y}" x2="{x}" y2="{plot_y + plot_h}" '
            f'stroke="#f2f2f2" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x}" y="{plot_y + plot_h + 18}" text-anchor="middle" '
            f'font-size="11" fill="{_LABEL_DARK}">{escape(str(d))}</text>'
        )

    # One polyline per series
    for label, vals in series.items():
        color = colors.get(label, _AXIS_GRAY)
        points = " ".join(f"{x_at(i)},{y_at(v)}" for i, v in enumerate(vals))
        parts.append(
            f'<polyline points="{points}" fill="none" '
            f'stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        # Draw a marker when there is a single point (a lone polyline is invisible)
        if len(vals) == 1:
            parts.append(
                f'<circle cx="{x_at(0)}" cy="{y_at(vals[0])}" r="3" fill="{color}"/>'
            )

    parts.append("</svg>")
    return "".join(parts)


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
