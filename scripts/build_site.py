#!/usr/bin/env python3
"""Build the GitHub Pages site: zip archives and generate index.html + downloads.html.

Usage:
    uv run python scripts/build_site.py
"""

import json
import os
import shutil
import zipfile
from datetime import date
from pathlib import Path

from utils import DATA_DIR, count_records
from charts import vertical_bar_chart, stacked_bar_chart, horizontal_bar_chart

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
DOWNLOADS_DIR = DOCS_DIR / "downloads"

CURRENT_YEAR = str(date.today().year)

MONTH_NAMES = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
}


# Color palette (forest green + amber)
PRIMARY = "#1e4d2b"       # deep forest green
PRIMARY_LIGHT = "#2d6a3e"  # slightly lighter for header hover/variant
ACCENT = "#e89b3c"         # warm amber
ACCENT_LIGHT = "#f4c37d"   # pale amber
PARTY_COLORS = {"D": "#2b6cb0", "R": "#c53030", "unknown": "#a0aec0"}


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


# Fixed timestamp for deterministic ZIP creation (2020-01-01 00:00:00).
# Using a fixed date_time on every entry means identical inputs produce
# identical ZIP bytes, so git only creates a new commit when data changes.
_FIXED_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def _add_to_zip(zf, file_path, arcname):
    """Add a file to a ZIP with a fixed timestamp for deterministic output."""
    with open(file_path, "rb") as f:
        data = f.read()
    info = zipfile.ZipInfo(filename=arcname, date_time=_FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    # Fix external attributes so file mode is stable across platforms
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def human_size(nbytes):
    """Format byte count as human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def scan_data():
    """Scan data/ for all daily JSONL files, aggregate by year and month.

    Returns dict: { year: { month: [{ day, filename, path, records }] } }
    """
    years = {}

    for year_dir in sorted(DATA_DIR.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year = year_dir.name
        years[year] = {}

        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            month_num = month_dir.name  # "01", "02", etc.
            days = []

            for f in sorted(month_dir.glob("*.jsonl")):
                rec_count = count_records(f)
                days.append({
                    "day": f.stem.split("-")[2],  # "2026-04-10" -> "10"
                    "filename": f.name,
                    "path": f,
                    "records": rec_count,
                })

            if days:
                years[year][month_num] = days

    return years


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


def build_downloads(years):
    """Create zip files for past years, copy current year files. Returns download metadata."""
    if DOWNLOADS_DIR.exists():
        shutil.rmtree(DOWNLOADS_DIR)
    DOWNLOADS_DIR.mkdir(parents=True)

    download_info = {}

    for year in sorted(years.keys()):
        months = years[year]
        if not months:
            continue

        year_total = sum(d["records"] for days in months.values() for d in days)

        if year == CURRENT_YEAR:
            # For current year: create monthly ZIPs of daily files
            month_downloads = []
            for month_num in sorted(months.keys()):
                days = months[month_num]
                month_name = MONTH_NAMES.get(month_num, month_num)
                month_records = sum(d["records"] for d in days)

                zip_name = f"{year}-{month_num}.zip"
                zip_path = DOWNLOADS_DIR / zip_name
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for d in sorted(days, key=lambda x: x["filename"]):
                        _add_to_zip(zf, d["path"], f"{year}/{month_num}/{d['filename']}")

                month_downloads.append({
                    "month_num": month_num,
                    "month_name": month_name,
                    "zip_name": zip_name,
                    "zip_size": human_size(zip_path.stat().st_size),
                    "records": month_records,
                    "days": len(days),
                })

            download_info[year] = {
                "type": "current",
                "months": month_downloads,
                "total_records": year_total,
            }
        else:
            # Past years: single yearly ZIP
            zip_name = f"{year}.zip"
            zip_path = DOWNLOADS_DIR / zip_name
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for month_num in sorted(months.keys()):
                    for d in sorted(months[month_num], key=lambda x: x["filename"]):
                        _add_to_zip(zf, d["path"], f"{year}/{month_num}/{d['filename']}")

            month_list = []
            for month_num in sorted(months.keys()):
                days = months[month_num]
                month_list.append({
                    "month_num": month_num,
                    "month_name": MONTH_NAMES.get(month_num, month_num),
                    "records": sum(d["records"] for d in days),
                })

            download_info[year] = {
                "type": "archive",
                "zip_name": zip_name,
                "zip_size": human_size(zip_path.stat().st_size),
                "months": month_list,
                "total_records": year_total,
            }

    return download_info


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


if __name__ == "__main__":
    main()
