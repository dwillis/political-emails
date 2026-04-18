#!/usr/bin/env python3
"""Build the GitHub Pages site: zip archives and generate index.html.

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

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
DOWNLOADS_DIR = DOCS_DIR / "downloads"

CURRENT_YEAR = str(date.today().year)

MONTH_NAMES = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
}


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


def generate_html(download_info, total_records, unique_domains):
    """Generate the index.html page."""
    year_range = f"{min(download_info.keys())}-{max(download_info.keys())}"

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

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Political Email Archive</title>
  <style>
    :root {{
      --navy: #1a2744;
      --navy-light: #2a3d5e;
      --gold: #c5a44e;
      --gold-light: #d4ba73;
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
      background: var(--navy);
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

    header h1 span {{
      color: var(--gold);
    }}

    header p {{
      font-size: 1.05rem;
      opacity: 0.85;
      max-width: 600px;
      margin: 0 auto 1rem;
    }}

    .header-links {{
      display: flex;
      gap: 1.5rem;
      justify-content: center;
      flex-wrap: wrap;
    }}

    .header-links a {{
      color: var(--gold-light);
      text-decoration: none;
      font-size: 0.9rem;
      border-bottom: 1px solid transparent;
    }}

    .header-links a:hover {{
      border-bottom-color: var(--gold-light);
    }}

    .stats {{
      display: flex;
      justify-content: center;
      gap: 2.5rem;
      padding: 1.2rem 1rem;
      background: white;
      border-bottom: 2px solid var(--gold);
      flex-wrap: wrap;
    }}

    .stat {{
      text-align: center;
    }}

    .stat-value {{
      font-family: "Libre Baskerville", "Georgia", serif;
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--navy);
    }}

    .stat-label {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #777;
    }}

    main {{
      max-width: 760px;
      margin: 2rem auto;
      padding: 0 1rem;
    }}

    h2 {{
      font-family: "Libre Baskerville", "Georgia", serif;
      font-size: 1.4rem;
      color: var(--navy);
      margin-bottom: 1rem;
      padding-bottom: 0.5rem;
      border-bottom: 2px solid var(--gold);
    }}

    details {{
      border: 1px solid var(--border);
      border-radius: 4px;
      margin-bottom: 0.5rem;
      background: white;
    }}

    summary {{
      padding: 0.8rem 1rem;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      user-select: none;
    }}

    summary:hover {{
      background: #f5f5f5;
    }}

    summary::-webkit-details-marker {{
      display: none;
    }}

    summary::before {{
      content: "\\25B6";
      font-size: 0.7rem;
      margin-right: 0.7rem;
      color: var(--gold);
      transition: transform 0.2s;
    }}

    details[open] > summary::before {{
      transform: rotate(90deg);
    }}

    .year {{
      font-family: "Libre Baskerville", "Georgia", serif;
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--navy);
    }}

    .year-meta {{
      font-size: 0.85rem;
      color: #888;
    }}

    details > :not(summary) {{
      padding: 0 1rem 1rem;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }}

    thead th {{
      text-align: left;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #888;
      padding: 0.5rem 0.5rem;
      border-bottom: 1px solid var(--border);
    }}

    td {{
      padding: 0.45rem 0.5rem;
      border-bottom: 1px solid #eee;
    }}

    .num {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}

    .dl-link {{
      color: var(--navy);
      text-decoration: none;
      font-weight: 500;
      font-size: 0.85rem;
      padding: 0.25rem 0.6rem;
      border: 1px solid var(--navy);
      border-radius: 3px;
      transition: all 0.15s;
    }}

    .dl-link:hover {{
      background: var(--navy);
      color: white;
    }}

    .dl-zip {{
      font-size: 0.95rem;
      padding: 0.4rem 1rem;
    }}

    .archive-row {{
      display: flex;
      align-items: center;
      gap: 1rem;
      margin-bottom: 0.5rem;
    }}

    .meta {{
      font-size: 0.85rem;
      color: #888;
    }}

    .months-covered {{
      font-size: 0.8rem;
      color: #aaa;
    }}

    footer {{
      text-align: center;
      padding: 2rem 1rem;
      font-size: 0.8rem;
      color: #999;
      border-top: 1px solid var(--border);
      margin-top: 2rem;
    }}

    footer a {{
      color: var(--navy-light);
    }}

    @media (max-width: 600px) {{
      header h1 {{ font-size: 1.8rem; }}
      .stats {{ gap: 1.5rem; }}
      .stat-value {{ font-size: 1.2rem; }}
      .archive-row {{ flex-direction: column; align-items: flex-start; gap: 0.3rem; }}
    }}
  </style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>An archive of political fundraising emails from {year_range}, with daily updates.</p>
    <div class="header-links">
      <a href="https://github.com/dwillis/political-emails">GitHub Repository</a>
    </div>
  </header>

  <div class="stats">
    <div class="stat">
      <div class="stat-value">{total_records:,}</div>
      <div class="stat-label">Emails</div>
    </div>
    <div class="stat">
      <div class="stat-value">{unique_domains:,}</div>
      <div class="stat-label">Unique Domains</div>
    </div>
    <div class="stat">
      <div class="stat-value">{len(download_info)}</div>
      <div class="stat-label">Years of Data</div>
    </div>
  </div>

  <main>
    <h2>Downloads</h2>
    <p style="margin-bottom: 1rem; font-size: 0.9rem; color: #666;">
      Data is stored as daily JSONL files (one JSON record per line).
      Current year data is available as monthly ZIP archives; past years as yearly ZIPs.
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
        print("  No data found in data/ directory. Run migrate_mbox.py first.")
        return

    stats = compute_stats(years)
    total_records = stats["total_records"]
    unique_domains = stats["unique_domains"]
    print(f"  Total records: {total_records:,}")
    print(f"  Unique domains: {unique_domains:,}")

    download_info = build_downloads(years)
    print(f"  Built downloads for {len(download_info)} years")

    html = generate_html(download_info, total_records, unique_domains)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = DOCS_DIR / "index.html"
    index_path.write_text(html)
    print(f"  Wrote {index_path}")

    total_downloads = len(list(DOWNLOADS_DIR.iterdir()))
    print(f"  {total_downloads} download files in {DOWNLOADS_DIR}")
    print("Done.")


if __name__ == "__main__":
    main()
