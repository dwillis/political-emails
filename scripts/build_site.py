#!/usr/bin/env python3
"""Build the GitHub Pages site: zip archives and generate index.html + downloads.html.

Usage:
    uv run python scripts/build_site.py
"""

import json
import os
import re
import shutil
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from utils import DATA_DIR, CONFIG_DIR, count_records
from committee_utils import committee_group_key
from charts import (
    vertical_bar_chart,
    stacked_bar_chart,
    horizontal_bar_chart,
    line_chart,
)

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
PARTY_COLORS = {"D": "#2b6cb0", "R": "#c53030", "OTH": "#6b46c1", "unknown": "#a0aec0"}
KEYWORD_LINE_COLORS = {"Total": ACCENT, **PARTY_COLORS}
PARTY_BUCKETS = ("D", "R", "OTH", "unknown")
TRACKED_SENDERS_MIN_EMAILS = 10
COMMITTEES_MIN_EMAILS = 10


def party_bucket(party):
    """Bucket a raw party value: D/R kept, I/G -> OTH, everything else unknown."""
    if party in ("D", "R"):
        return party
    if party in ("I", "G"):
        return "OTH"
    return "unknown"


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

    .dl-row {{
      display: flex; align-items: center; gap: 1rem;
      padding: 0.6rem 0.8rem;
      background: white;
      border: 1px solid var(--border);
      border-radius: 4px;
      margin-bottom: 0.5rem;
      flex-wrap: wrap;
    }}
    .dl-label {{ font-weight: 600; color: var(--primary); }}
    .dl-meta {{ color: #888; font-size: 0.9rem; }}

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


def load_tracked_keywords():
    """Load tracked-keyword patterns from config/tracked_keywords.json.

    Returns dict { keyword_slug: regex_pattern_string }. Missing file -> {}.
    """
    path = CONFIG_DIR / "tracked_keywords.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_tracked_people():
    """Load configured people and their reviewed mention patterns."""
    path = CONFIG_DIR / "tracked_people.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


_DISCLAIMER_START_RE = re.compile(r"\bpaid\s+for\s+(?:by|and)\b", re.IGNORECASE)
_FOOTER_START_RE = re.compile(
    r"\b(?:unsubscribe|email\s+preferences|privacy\s+policy|"
    r"this\s+(?:email|message)\s+was\s+sent\s+to)\b",
    re.IGNORECASE,
)


def mention_text(rec):
    """Return the subject and campaign copy eligible for person matching.

    ``clean_body`` removes most markup. Explicitly remove the disclaimer and
    remaining footer so a name in legal text cannot count as a campaign mention.
    """
    body = str(rec.get("clean_body") or rec.get("body") or "")
    starts = [m.start() for pattern in (_DISCLAIMER_START_RE, _FOOTER_START_RE)
              if (m := pattern.search(body))]
    if starts:
        body = body[:min(starts)]
    return f"{rec.get('subject', '')} {body}"


def _week_start(date_key):
    """Return the Monday that starts the calendar week containing date_key."""
    day = date.fromisoformat(date_key)
    return (day - timedelta(days=day.weekday())).isoformat()


def _new_sender_mention_tracker(people):
    compiled = {
        slug: [re.compile(pattern, re.IGNORECASE) for pattern in spec.get("patterns", [])]
        for slug, spec in people.items()
    }
    # Tallies are keyed by a normalized committee key so name variants that
    # differ only in case or trailing punctuation ("...Inc" vs "...Inc.")
    # aggregate into one entity. ``display`` counts the raw variants behind each
    # key so the most common spelling can be shown.
    tallies = defaultdict(lambda: defaultdict(lambda: {
        "total_emails": 0, "matching_emails": 0, "party_counts": defaultdict(int),
    }))
    display = defaultdict(Counter)
    return compiled, tallies, display


def _add_sender_mention(tracker, rec, date_key):
    compiled, tallies, display = tracker
    committee = rec.get("committee")
    if not rec.get("disclaimer") or not committee:
        return
    key = committee_group_key(rec)
    if not key:
        return
    # the registry's frozen canonical name is the display spelling; verbatim
    # variants only surface for records no entity resolved
    display[key][str(rec.get("committee_canonical") or committee).strip()] += 1
    week = _week_start(date_key)
    text = mention_text(rec)
    for slug, patterns in compiled.items():
        bucket = tallies[slug][(week, key)]
        bucket["total_emails"] += 1
        bucket["party_counts"][party_bucket(rec.get("party"))] += 1
        if any(pattern.search(text) for pattern in patterns):
            bucket["matching_emails"] += 1


def _display_name(variants):
    """Pick the representative spelling for a committee key: most common, then
    alphabetical for a deterministic tie-break independent of scan order."""
    return min(variants.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _sender_mention_result(people, tracker):
    _, tallies, display = tracker
    names = {key: _display_name(variants) for key, variants in display.items()}
    result = {"people": {}}
    for slug, spec in people.items():
        rows = []
        for (week, key), tally in sorted(tallies[slug].items()):
            party_counts = tally["party_counts"]
            party = max(PARTY_BUCKETS, key=lambda bucket: party_counts[bucket])
            rows.append({
                "week": week,
                "committee": names.get(key, key),
                "party": party,
                "total_emails": tally["total_emails"],
                "matching_emails": tally["matching_emails"],
            })
        result["people"][slug] = {"name": spec.get("name", slug), "weekly": rows}
    return result


def compute_sender_mentions(years, people=None):
    """Aggregate disclaimer committee mentions by person and calendar week."""
    if people is None:
        people = load_tracked_people()
    tracker = _new_sender_mention_tracker(people)

    for months in years.values():
        for days in months.values():
            for day_info in days:
                date_key = Path(day_info["path"]).stem
                with open(day_info["path"]) as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        _add_sender_mention(tracker, rec, date_key)

    return _sender_mention_result(people, tracker)


def _source_bucket(source):
    """Collapse committee_source values into a few display buckets."""
    text = str(source or "")
    if text.startswith("llm"):
        return "llm"
    if text in ("human", "disclaimer", "backfill"):
        return text
    return "other"


def _new_committee_tracker():
    return {"committees": {}, "monthly": defaultdict(int)}


def _add_committee(tracker, rec, date_key):
    """Tally one email toward its committee (any record with a committee,
    whether or not it carries a disclaimer)."""
    committee = rec.get("committee")
    if not committee:
        return
    key = committee_group_key(rec)
    if not key:
        return
    entry = tracker["committees"].get(key)
    if entry is None:
        entry = tracker["committees"][key] = {
            "variants": Counter(), "fec_id": None,
            "first_seen": date_key, "last_seen": date_key,
            "total": 0, "party_counts": defaultdict(int), "sources": Counter(),
            "domains": {},
        }
    entry["variants"][str(committee).strip()] += 1
    if rec.get("committee_fec_id"):
        entry["fec_id"] = rec["committee_fec_id"]
    entry["first_seen"] = min(entry["first_seen"], date_key)
    entry["last_seen"] = max(entry["last_seen"], date_key)
    entry["total"] += 1
    entry["party_counts"][party_bucket(rec.get("party"))] += 1
    entry["sources"][_source_bucket(rec.get("committee_source"))] += 1
    tracker["monthly"][(date_key[:7], key)] += 1
    domain = str(rec.get("domain") or "").strip().lower()
    if domain:
        dom = entry["domains"].get(domain)
        if dom is None:
            entry["domains"][domain] = [1, date_key, date_key]
        else:
            dom[0] += 1
            dom[1] = min(dom[1], date_key)
            dom[2] = max(dom[2], date_key)


def _committee_result(tracker):
    """Serialize the tracker for committees.json.

    ``committees`` is sorted by total emails (desc, then name). ``monthly`` rows
    are compact ``[YYYY-MM, committee_index, emails]`` triples that index into it.
    """
    entries = []
    domains = {}
    for key, entry in tracker["committees"].items():
        domains[key] = sorted(
            ([d, *v] for d, v in entry["domains"].items()),
            key=lambda row: (-row[1], row[0]),
        )
        counts = entry["party_counts"]
        entries.append((key, {
            "name": _display_name(entry["variants"]),
            "party": max(PARTY_BUCKETS, key=lambda bucket: counts[bucket]),
            "fec_id": entry["fec_id"],
            "first_seen": entry["first_seen"],
            "last_seen": entry["last_seen"],
            "total": entry["total"],
            "sources": dict(sorted(entry["sources"].items())),
        }))
    entries.sort(key=lambda kv: (-kv[1]["total"], kv[1]["name"], kv[0]))
    index = {key: i for i, (key, _) in enumerate(entries)}
    monthly = sorted(
        [month, index[key], n] for (month, key), n in tracker["monthly"].items()
    )
    return {
        "committees": [row for _, row in entries], "monthly": monthly,
        # Per committee (aligned with ``committees``): [domain, emails, first, last]
        # rows. Written to committee_domains.json, not committees.json.
        "domains": [domains[key] for key, _ in entries],
    }


def committee_concentration(result):
    """How concentrated email volume is across committees.

    Returns total_committees, total_emails, top10/top50/top100 shares (0-1),
    half_rank (fewest committees whose emails reach 50% of the total), and
    ``segments``: disjoint (label, emails) bars that sum to total_emails.
    """
    totals = sorted((c["total"] for c in result["committees"]), reverse=True)
    grand = sum(totals)
    out = {
        "total_committees": len(totals), "total_emails": grand,
        "top10_share": 0.0, "top50_share": 0.0, "top100_share": 0.0,
        "half_rank": 0, "segments": [],
    }
    if not grand:
        return out
    for n in (10, 50, 100):
        out[f"top{n}_share"] = sum(totals[:n]) / grand
    running = 0
    for rank, value in enumerate(totals, 1):
        running += value
        if running * 2 >= grand:
            out["half_rank"] = rank
            break
    bounds = [(0, 10, "Top 10"), (10, 50, "Ranks 11–50"),
              (50, 100, "Ranks 51–100"), (100, len(totals), "All others")]
    for lo, hi, label in bounds:
        emails = sum(totals[lo:hi])
        if emails:
            out["segments"].append((f"{label} ({100 * emails / grand:.0f}%)", emails))
    return out


def committees_first_seen_by_year(result):
    """Count each committee once, in the year it first appears."""
    counts = Counter(c["first_seen"][:4] for c in result["committees"])
    return dict(sorted(counts.items()))


def build_committee_charts(result):
    """Static dashboard charts for committee concentration and new committees."""
    if not result or not result["committees"]:
        return ""
    conc = committee_concentration(result)
    chart_concentration = horizontal_bar_chart(
        conc["segments"], title="Share of email by committee rank", color=ACCENT,
        height=230,
    )
    chart_new = vertical_bar_chart(
        committees_first_seen_by_year(result),
        title="New committees per year", color=ACCENT,
    )
    note = (
        f'<p class="chart-note">{conc["total_committees"]:,} committees have sent email. '
        f'The top 10 account for {100 * conc["top10_share"]:.0f}% of it, and half of all '
        f'email comes from {conc["half_rank"]:,} committees. '
        f'<a href="committees.html">Explore all committees →</a></p>'
    )
    return f"{chart_concentration}\n    {note}\n    {chart_new}"


def compute_stats(years, keyword_patterns=None, tracked_people=None):
    """Single-pass scan over JSONL files, return rich stats dict.

    years: output of scan_data().
    keyword_patterns: dict { keyword_slug: regex_pattern_string }. Defaults to
        config/tracked_keywords.json. Each email counts at most once per keyword
        per day; matching is case-insensitive over subject + body.

    Returns dict with:
        total_records, disclaimer_count,
        party_counts: {"D", "R", "unknown"},
        unique_domains: int,
        by_year: { year: {total, D, R, unknown, disclaimer} },
        top_domains: [(domain, count), ...]  sorted desc, top 10,
        keyword_daily: { keyword: { "YYYY-MM-DD": {"D", "R", "unknown"} } },
        committees: {committees: [...], monthly: [[YYYY-MM, index, emails], ...]}.
    """
    if keyword_patterns is None:
        keyword_patterns = load_tracked_keywords()
    if tracked_people is None:
        tracked_people = load_tracked_people()
    compiled = {
        kw: re.compile(pat, re.IGNORECASE) for kw, pat in keyword_patterns.items()
    }

    total_records = 0
    disclaimer_count = 0
    party_counts = {b: 0 for b in PARTY_BUCKETS}
    domain_counter = Counter()
    by_year = {}
    keyword_daily = {kw: {} for kw in compiled}
    sender_tracker = _new_sender_mention_tracker(tracked_people)
    committee_tracker = _new_committee_tracker()
    all_dates = set()

    for year, months in years.items():
        year_stats = {"total": 0, **{b: 0 for b in PARTY_BUCKETS}, "disclaimer": 0}
        for month_num, days in months.items():
            for d in days:
                date_key = Path(d["path"]).stem  # "YYYY-MM-DD"
                if d["records"]:
                    all_dates.add(date_key)
                with open(d["path"]) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        _add_sender_mention(sender_tracker, rec, date_key)
                        _add_committee(committee_tracker, rec, date_key)

                        total_records += 1
                        year_stats["total"] += 1

                        bucket = party_bucket(rec.get("party"))
                        party_counts[bucket] += 1
                        year_stats[bucket] += 1

                        if rec.get("disclaimer"):
                            disclaimer_count += 1
                            year_stats["disclaimer"] += 1

                        domain = rec.get("domain")
                        if domain:
                            domain_counter[domain] += 1

                        if compiled:
                            haystack = (
                                f"{rec.get('subject', '')} {rec.get('body', '')}"
                            )
                            for kw, pattern in compiled.items():
                                if pattern.search(haystack):
                                    day_tally = keyword_daily[kw].setdefault(
                                        date_key, {b: 0 for b in PARTY_BUCKETS}
                                    )
                                    day_tally[bucket] += 1
        if year_stats["total"] > 0:
            by_year[year] = year_stats

    return {
        "total_records": total_records,
        "disclaimer_count": disclaimer_count,
        "party_counts": party_counts,
        "unique_domains": len(domain_counter),
        "by_year": by_year,
        "top_domains": domain_counter.most_common(10),
        "keyword_daily": keyword_daily,
        "sender_mentions": _sender_mention_result(tracked_people, sender_tracker),
        "committees": _committee_result(committee_tracker),
        "all_dates": sorted(all_dates),
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


_PREVIEW_JUNK_RE = re.compile(r"(?:\s*\|)+|-{2,}|#+|\*{2,}|_{2,}")


def _clean_preview(text, limit=200):
    """Strip html2text table/markdown debris, then word-boundary truncate.

    Email bodies converted from HTML carry pipe-delimited table scaffolding
    ("| | | |"), horizontal rules ("---") and markdown emphasis that read as
    noise in a one-line preview.
    """
    if not text:
        return ""
    text = _PREVIEW_JUNK_RE.sub(" ", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut + "…"


def build_recent(hours=24):
    """Read the last 24h of records from today/yesterday's JSONL, write docs/recent.json.

    Returns a summary dict: {count, start_iso, end_iso, window_hours}.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    candidate_dates = [(now - timedelta(days=offset)).date() for offset in range(2)]
    candidate_paths = [
        DATA_DIR / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.year:04d}-{d.month:02d}-{d.day:02d}.jsonl"
        for d in candidate_dates
    ]

    records = []
    for path in candidate_paths:
        if not path.exists():
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                date_str = rec.get("date")
                if not date_str:
                    continue
                try:
                    ts = datetime.fromisoformat(date_str)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                preview = _clean_preview(rec.get("clean_body") or rec.get("body") or "")
                records.append({
                    "ts": ts.astimezone(timezone.utc).isoformat(),
                    "name": rec.get("name") or "",
                    "email": rec.get("email") or "",
                    "domain": rec.get("domain") or "",
                    "subject": rec.get("subject") or "",
                    "preview": preview,
                    "committee": rec.get("committee") or "",
                    "party": rec.get("party"),
                    "disclaimer": bool(rec.get("disclaimer")),
                })

    records.sort(key=lambda r: r["ts"], reverse=True)

    summary = {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "start_iso": cutoff.isoformat(),
        "end_iso": now.isoformat(),
        "count": len(records),
    }
    payload = {**summary, "emails": records}

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "recent.json").write_text(json.dumps(payload, ensure_ascii=False))
    return summary


KEYWORD_CHART_WINDOW_DAYS = 365


def _keyword_title(keyword):
    """Human-readable chart title for a keyword slug."""
    pretty = keyword.replace("_", " ")
    return f'"{pretty}" mentions per week, by party (last 12 months)'


def _daily_span(all_dates, window_days):
    """Continuous list of ISO dates for the trailing window of the archive.

    Ends on the most recent date in ``all_dates`` and includes every calendar
    day back to ``window_days`` earlier (or the archive's first day, whichever
    is later). Returns [] when there are no dates.
    """
    if not all_dates:
        return []
    first = date.fromisoformat(all_dates[0])
    end = date.fromisoformat(all_dates[-1])
    start = max(first, end - timedelta(days=window_days - 1))
    span = []
    cursor = start
    while cursor <= end:
        span.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return span


def _weekly_buckets(span):
    """Group a continuous daily span into weeks keyed by their Monday.

    Returns a list of (week_start_iso, [iso_dates in that week]) in order.
    The first and last buckets may be partial weeks.
    """
    weeks = []
    for d in span:
        day = date.fromisoformat(d)
        week_start = (day - timedelta(days=day.weekday())).isoformat()
        if not weeks or weeks[-1][0] != week_start:
            weeks.append((week_start, []))
        weeks[-1][1].append(d)
    return weeks


def build_keyword_charts(stats, window_days=KEYWORD_CHART_WINDOW_DAYS):
    """Build one inline-SVG line chart per tracked keyword.

    Aggregates daily counts into calendar weeks (Monday start) over the
    archive's trailing ``window_days`` (missing days count as zero), with one
    line per party plus a total line. Returns a single HTML string (possibly
    empty if there is no keyword data).
    """
    keyword_daily = stats.get("keyword_daily") or {}
    archive_dates = stats.get("all_dates") or []
    if not keyword_daily or not archive_dates:
        return ""

    weeks = _weekly_buckets(_daily_span(archive_dates, window_days))
    # Partial weeks at either edge under-count and read as sudden drops;
    # plot complete weeks only (unless the archive has no complete week yet).
    complete = [w for w in weeks if len(w[1]) == 7]
    weeks = complete or weeks
    week_starts = [w for w, _ in weeks]

    charts = []
    for keyword, daily in keyword_daily.items():
        series = {
            party: [
                sum(daily.get(d, {}).get(party, 0) for d in days)
                for _, days in weeks
            ]
            for party in PARTY_BUCKETS
        }
        # Skip a keyword that never matched anything.
        if not any(any(vals) for vals in series.values()):
            continue
        # Total first so the party lines draw on top of it.
        totals = [sum(week_vals) for week_vals in zip(*series.values())]
        series = {"Total": totals, **series}
        charts.append(
            line_chart(
                week_starts,
                series,
                KEYWORD_LINE_COLORS,
                title=_keyword_title(keyword),
            )
        )
    return "\n".join(charts)


def generate_dashboard_html(stats, download_info, recent_summary):
    """Generate the dashboard home page (index.html)."""
    year_range = (
        f"{min(stats['by_year'].keys())}–{max(stats['by_year'].keys())}"
        if stats["by_year"] else "—"
    )

    total = stats["total_records"] or 1  # avoid div-by-zero
    disclaimer_pct = round(100 * stats["disclaimer_count"] / total)
    d_pct = round(100 * stats["party_counts"]["D"] / total)
    r_pct = round(100 * stats["party_counts"]["R"] / total)
    oth_pct = round(100 * stats["party_counts"]["OTH"] / total)

    # Prepare chart data
    year_totals = {y: s["total"] for y, s in sorted(stats["by_year"].items())}
    year_party = {
        y: {b: s[b] for b in PARTY_BUCKETS}
        for y, s in sorted(stats["by_year"].items())
    }

    chart_emails_per_year = vertical_bar_chart(
        year_totals, title="Emails per year", color=ACCENT
    )
    chart_party_by_year = stacked_bar_chart(
        year_party,
        categories=list(PARTY_BUCKETS),
        colors=PARTY_COLORS,
        title="Party breakdown by year",
    )
    chart_top_domains = horizontal_bar_chart(
        stats["top_domains"], title="Top 10 sender domains", color=ACCENT
    )

    committee_charts = build_committee_charts(stats.get("committees"))
    keyword_charts = build_keyword_charts(stats)

    topics = sorted(load_tracked_keywords().keys())
    if topics:
        links = " · ".join(
            f'<a href="topic/{t}/">{t}</a>' for t in topics
        )
        topics_nav = (
            f'<h2>Topics</h2>\n'
            f'<p class="topics-nav">Recent emails with a campaign disclaimer, '
            f'by keyword: {links}</p>'
        )
    else:
        topics_nav = ""

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
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    @media (min-width: 720px) {
      .stats { grid-template-columns: repeat(5, 1fr); }
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
    .dl-all { margin-top: 0.6rem; }
    .chart-note { margin: -1rem 0 2rem; color: #555; font-size: 0.9rem; }

    .recent-meta {
      font-size: 0.85rem; color: #777; margin-bottom: 0.8rem;
    }
    .filter-bar {
      display: flex; flex-wrap: wrap; gap: 0.4rem 0.8rem;
      align-items: center;
      margin-bottom: 1rem;
      padding: 0.6rem 0.8rem;
      background: white;
      border: 1px solid var(--border);
      border-radius: 4px;
    }
    .filter-group { display: flex; gap: 0.3rem; align-items: center; }
    .filter-group .label {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #888;
      margin-right: 0.25rem;
    }
    .filter-chip {
      font: inherit;
      cursor: pointer;
      background: white;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0.2rem 0.7rem;
      font-size: 0.85rem;
      color: var(--text);
    }
    .filter-chip:hover { border-color: var(--primary); }
    .filter-chip.active {
      background: var(--primary);
      color: white;
      border-color: var(--primary);
    }
    .filter-count { margin-left: auto; font-size: 0.8rem; color: #888; }

    #recent-list { display: flex; flex-direction: column; gap: 0.3rem; }
    .email-row {
      background: white;
      border: 1px solid var(--border);
      border-left: 3px solid var(--border);
      border-radius: 4px;
    }
    .email-row.party-D { border-left-color: #2b6cb0; }
    .email-row.party-R { border-left-color: #c53030; }
    .email-row.party-OTH { border-left-color: #6b46c1; }
    .email-row.party-unknown { border-left-color: #a0aec0; }
    .email-row.hidden { display: none; }
    .email-head {
      display: grid;
      grid-template-columns: auto auto minmax(0, 1fr);
      gap: 0.6rem; align-items: center;
      padding: 0.5rem 0.8rem;
      cursor: pointer;
      font-size: 0.86rem;
      list-style: none;
    }
    .email-head::-webkit-details-marker { display: none; }
    .email-head .ts { color: #999; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .email-head .dot {
      width: 9px; height: 9px; border-radius: 50%; flex: none;
      background: #cbd5e0; box-shadow: inset 0 0 0 1px #a0aec0;
    }
    .email-head .dot.party-D { background: #2b6cb0; box-shadow: none; }
    .email-head .dot.party-R { background: #c53030; box-shadow: none; }
    .email-head .dot.party-OTH { background: #6b46c1; box-shadow: none; }
    .email-head .dot.party-unknown { background: transparent; }
    .email-head .line {
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
    }
    .email-head .identity { color: var(--text); font-weight: 600; }
    .email-head .subject { color: #666; }
    .email-body {
      display: none;
      padding: 0 0.8rem 0.7rem 0.8rem;
      border-top: 1px solid var(--border);
      margin-top: -1px;
    }
    details[open] > .email-body { display: block; }
    .email-body .preview { font-size: 0.86rem; color: #555; margin: 0.5rem 0; }
    .email-body .detail {
      display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
      font-size: 0.78rem; color: #888;
    }
    .email-body .detail .committee { color: var(--text); font-weight: 600; }
    .party-badge {
      display: inline-block;
      font-size: 0.68rem; font-weight: 700; letter-spacing: 0.05em;
      padding: 0.02rem 0.35rem; border-radius: 3px; color: white;
    }
    .party-badge.D { background: #2b6cb0; }
    .party-badge.R { background: #c53030; }
    .party-badge.OTH { background: #6b46c1; }
    .party-badge.unknown { background: #a0aec0; }
    .disclaimer-tag {
      font-size: 0.7rem; color: var(--primary);
      border: 1px solid var(--primary); border-radius: 3px; padding: 0.02rem 0.35rem;
    }
    .show-all-btn {
      display: block; width: 100%; margin-top: 0.5rem; padding: 0.5rem;
      background: white; border: 1px solid var(--border); border-radius: 4px;
      color: var(--primary); font: inherit; font-size: 0.85rem; cursor: pointer;
    }
    .show-all-btn:hover { border-color: var(--primary); }
    .recent-empty, .recent-error {
      padding: 1rem; background: white;
      border: 1px dashed var(--border); border-radius: 4px;
      color: #777; font-size: 0.9rem;
    }
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
      <a href="committees.html">Committees</a>
      <a href="sender-mentions.html">Sender mentions</a>
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
      <div class="stat-card">
        <div class="stat-value">{stats["party_counts"]["OTH"]:,}</div>
        <div class="stat-label">Other (I/G)</div>
        <div class="stat-meta">{oth_pct}% of total</div>
      </div>
    </section>

    {chart_emails_per_year}
    {chart_party_by_year}
    {chart_top_domains}
    {committee_charts}
    {keyword_charts}

    {topics_nav}

    <h2>Downloads</h2>
    {current_month_link}
    {latest_year_link}
    <p class="dl-all"><a href="downloads.html">See all monthly and yearly archives →</a></p>

    <h2>Latest emails — past 24 hours</h2>
    <p class="recent-meta">{recent_summary["count"]:,} emails received in the last 24 hours (as of {recent_summary["end_iso"][:16].replace("T", " ")} UTC).</p>
    <div class="filter-bar">
      <div class="filter-group">
        <span class="label">Party</span>
        <button class="filter-chip active" data-filter="party" data-value="all">All</button>
        <button class="filter-chip" data-filter="party" data-value="D">D</button>
        <button class="filter-chip" data-filter="party" data-value="R">R</button>
        <button class="filter-chip" data-filter="party" data-value="OTH">Other</button>
        <button class="filter-chip" data-filter="party" data-value="unknown">Unknown</button>
      </div>
      <div class="filter-group">
        <span class="label">Disclaimer</span>
        <button class="filter-chip active" data-filter="disclaimer" data-value="any">Any</button>
        <button class="filter-chip" data-filter="disclaimer" data-value="yes">With</button>
        <button class="filter-chip" data-filter="disclaimer" data-value="no">Without</button>
      </div>
      <span class="filter-count" id="filter-count"></span>
    </div>
    <div id="recent-list"><p class="recent-meta">Loading…</p></div>
  </main>

  <footer>
    Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>.
    Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.
  </footer>
  <script>
  (function() {{
    var state = {{ party: "all", disclaimer: "any", showAll: false }};
    var listEl = document.getElementById("recent-list");
    var countEl = document.getElementById("filter-count");

    function escapeHtml(s) {{
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }}

    function fmtTime(iso) {{
      var d = new Date(iso);
      if (isNaN(d)) return "";
      var hh = String(d.getUTCHours()).padStart(2, "0");
      var mm = String(d.getUTCMinutes()).padStart(2, "0");
      return hh + ":" + mm + " UTC";
    }}

    function partyKey(p) {{
      if (p === "D" || p === "R") return p;
      if (p === "I" || p === "G") return "OTH";
      return "unknown";
    }}

    var allEmails = [];
    var CAP = 50;

    function matches(e) {{
      var pk = partyKey(e.party);
      var d = e.disclaimer ? "yes" : "no";
      return (state.party === "all" || state.party === pk) &&
             (state.disclaimer === "any" || state.disclaimer === d);
    }}

    function rowHtml(e) {{
      var pk = partyKey(e.party);
      var identity = e.committee || e.name || e.email || "(unknown sender)";
      var line = '<span class="line"><span class="identity">' + escapeHtml(identity) + '</span>' +
                 (e.subject ? ' <span class="subject">— ' + escapeHtml(e.subject) + '</span>' : '') +
                 '</span>';
      var disc = e.disclaimer ? '<span class="disclaimer-tag">disclaimer</span>' : '';
      var committee = e.committee ? '<span class="committee">Paid for by ' + escapeHtml(e.committee) + '</span>' : '';
      var sender = escapeHtml(e.name || '(unknown sender)') +
                   (e.email ? ' &lt;' + escapeHtml(e.email) + '&gt;' : '');
      var preview = e.preview ? '<div class="preview">' + escapeHtml(e.preview) + '</div>' : '';
      return '<details class="email-row party-' + pk + '"' +
             ' data-party="' + pk + '" data-disclaimer="' + (e.disclaimer ? "yes" : "no") + '">' +
        '<summary class="email-head">' +
          '<span class="ts">' + escapeHtml(fmtTime(e.ts)) + '</span>' +
          '<span class="dot party-' + pk + '"></span>' +
          line +
        '</summary>' +
        '<div class="email-body">' + preview +
          '<div class="detail">' + committee + '<span class="sender">' + sender + '</span>' + disc + '</div>' +
        '</div>' +
      '</details>';
    }}

    function render() {{
      if (!allEmails.length) {{
        listEl.innerHTML = '<p class="recent-empty">No emails in the last 24 hours.</p>';
        countEl.textContent = "";
        return;
      }}
      var filtered = allEmails.filter(matches);
      if (!filtered.length) {{
        listEl.innerHTML = '<p class="recent-empty">No emails match these filters.</p>';
        countEl.textContent = "Showing 0 of " + allEmails.length.toLocaleString();
        return;
      }}
      var shown = state.showAll ? filtered : filtered.slice(0, CAP);
      var html = shown.map(rowHtml).join("");
      if (!state.showAll && filtered.length > CAP) {{
        html += '<button class="show-all-btn" id="show-all">Show all ' +
                filtered.length.toLocaleString() + ' emails</button>';
      }}
      listEl.innerHTML = html;
      var btn = document.getElementById("show-all");
      if (btn) btn.addEventListener("click", function() {{ state.showAll = true; render(); }});
      countEl.textContent = "Showing " + shown.length.toLocaleString() +
                            " of " + filtered.length.toLocaleString();
    }}

    document.querySelectorAll(".filter-chip").forEach(function(btn) {{
      btn.addEventListener("click", function() {{
        var filter = btn.getAttribute("data-filter");
        state[filter] = btn.getAttribute("data-value");
        state.showAll = false;
        document.querySelectorAll('.filter-chip[data-filter="' + filter + '"]').forEach(function(b) {{
          b.classList.toggle("active", b === btn);
        }});
        render();
      }});
    }});

    fetch("recent.json", {{ cache: "no-cache" }})
      .then(function(r) {{ if (!r.ok) throw new Error(r.status); return r.json(); }})
      .then(function(data) {{ allEmails = data.emails || []; render(); }})
      .catch(function() {{
        listEl.innerHTML = '<p class="recent-error">Could not load recent emails. ' +
          'See <a href="downloads.html">downloads</a> for the full archive.</p>';
        countEl.textContent = "";
      }});
  }})();
  </script>
</body>
</html>"""

    return html


def _party_label(party):
    return {"D": "D", "R": "R", "I": "OTH", "G": "OTH"}.get(party, "Unknown")


def _format_long_date(day):
    """Format a "YYYY-MM-DD" string as e.g. "August 8, 2026"."""
    d = date.fromisoformat(day)
    return f"{MONTH_NAMES[f'{d.month:02d}']} {d.day}, {d.year}"


def generate_topic_html(topic, day, emails, generated_iso):
    """Generate a topic page (docs/topic/<topic>/index.html).

    Args:
        topic: keyword slug (e.g. "datacenter").
        day: "YYYY-MM-DD" the shown emails are from, or None when there are none.
        emails: list of dicts with keys date, name, email, party, subject, image
            (image is a PNG filename relative to the page).
        generated_iso: build timestamp for the footer note.

    Reuses the site chrome. Links back to the home page with "../../" since the
    page lives two directories deep.
    """
    from html import escape

    pretty = topic.replace("_", " ")

    topic_css = """
    .topic-intro { color: #555; margin-bottom: 1.5rem; }
    .email-card {
      background: white; border: 1px solid var(--border); border-radius: 6px;
      margin-bottom: 1.5rem; overflow: hidden;
    }
    .email-head {
      display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem 1rem;
      padding: 0.9rem 1.1rem; border-bottom: 1px solid var(--border);
      background: #faf9f6;
    }
    .email-subject { font-weight: 700; color: var(--primary); flex: 1 1 100%; }
    .email-meta { font-size: 0.85rem; color: #666; }
    .party-badge {
      display: inline-block; min-width: 1.4rem; text-align: center;
      padding: 0.1rem 0.5rem; border-radius: 3px; font-size: 0.8rem;
      font-weight: 700; color: white;
    }
    .email-shot {
      max-height: 620px; overflow-y: auto; background: #f4f4f2;
      text-align: center;
    }
    .email-shot img { width: 100%; max-width: 600px; display: block; margin: 0 auto; }
    .topic-empty { color: #777; font-style: italic; }
    """

    cards = []
    for e in emails:
        color = PARTY_COLORS[party_bucket(e.get("party"))]
        when = str(e.get("date", ""))[:16].replace("T", " ")
        sender = e.get("name") or e.get("email") or "Unknown sender"
        cards.append(f"""
      <article class="email-card">
        <div class="email-head">
          <span class="email-subject">{escape(str(e.get("subject") or "(no subject)"))}</span>
          <span class="email-meta">{escape(when)} UTC</span>
          <span class="email-meta">{escape(str(sender))} &lt;{escape(str(e.get("email") or ""))}&gt;</span>
          <span class="party-badge" style="background:{color}">{_party_label(e.get("party"))}</span>
        </div>
        <div class="email-shot">
          <img src="{escape(str(e.get("image")))}" alt="Screenshot of email: {escape(str(e.get("subject") or ""))}" loading="lazy">
        </div>
      </article>""")

    if emails:
        intro = (
            f'Recent emails mentioning "{escape(pretty)}" with a campaign '
            f'disclaimer, from {escape(_format_long_date(day))}.'
        )
        body = "\n".join(cards)
    else:
        intro = f'No emails mentioning "{escape(pretty)}" with a campaign disclaimer yet.'
        body = '<p class="topic-empty">Nothing to show yet — check back after the next daily update.</p>'

    gen = escape(str(generated_iso)[:16].replace("T", " "))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(pretty)} emails — Political Email Archive</title>
  <style>{SHARED_CSS}{topic_css}</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>Recent "{escape(pretty)}" emails.</p>
    <div class="header-links">
      <a href="../../">Home</a>
      <a href="../../downloads.html">All Downloads</a>
      <a href="https://github.com/dwillis/political-emails">GitHub</a>
    </div>
  </header>

  <main>
    <h2>"{escape(pretty)}" — recent emails</h2>
    <p class="topic-intro">{intro}</p>
    {body}
  </main>

  <footer>
    Generated {gen} UTC. Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>.
    Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.
  </footer>
</body>
</html>"""


def generate_sender_mentions_html(generated_iso):
    """Generate the client-rendered disclaimer sender mention tracker."""
    tracker_css = """
    .tracker-intro { color: #555; margin-bottom: 1rem; }
    .tracker-controls { display: flex; flex-wrap: wrap; gap: 1rem; align-items: end; margin: 1rem 0; }
    .tracker-controls label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.85rem; font-weight: 600; }
    .tracker-controls select { font: inherit; padding: 0.35rem 0.5rem; border: 1px solid var(--border); border-radius: 4px; background: white; }
    .tracker-summary { color: #666; font-size: 0.9rem; margin: 0.5rem 0 1rem; }
    .tracker-chart { width: 100%; min-height: 280px; background: white; border: 1px solid var(--border); border-radius: 4px; padding: 0.5rem; }
    .mention-table { width: 100%; border-collapse: collapse; background: white; font-size: 0.9rem; }
    .mention-table th, .mention-table td { padding: 0.55rem 0.65rem; border-bottom: 1px solid var(--border); text-align: left; }
    .mention-table th { color: var(--primary); font-size: 0.75rem; letter-spacing: 0.05em; text-transform: uppercase; }
    .mention-table td.num, .mention-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .mention-table .party { font-weight: 700; }
    .tracker-error { color: #8b1e1e; }
    """
    generated = escape(str(generated_iso)[:16].replace("T", " "))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sender mentions — Political Email Archive</title>
  <style>{SHARED_CSS}{tracker_css}</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>How often disclaimer-identified political committees mention tracked people.</p>
    <div class="header-links"><a href="index.html">Home</a><a href="downloads.html">All Downloads</a><a href="committees.html">Committees</a><a href="https://github.com/dwillis/political-emails">GitHub</a></div>
  </header>
  <main>
    <h2>Sender mentions</h2>
    <p class="tracker-intro">Counts distinct emails with a campaign disclaimer. Mentions are matched in the subject and message copy, excluding legal and unsubscribe footers. Committees need at least {TRACKED_SENDERS_MIN_EMAILS} qualifying emails in the selected period to appear below.</p>
    <p class="tracker-intro"><a href="sender_mentions.json">Download the complete weekly data (JSON)</a></p>
    <div class="tracker-controls">
      <label>Person <select id="person"></select></label>
      <label>Period <select id="period"><option value="52">Last 52 weeks</option><option value="0">All history</option></select></label>
    </div>
    <p class="tracker-summary" id="summary">Loading…</p>
    <svg class="tracker-chart" id="chart" viewBox="0 0 800 280" role="img" aria-label="Weekly mention rate"></svg>
    <h2>Committees</h2>
    <div id="table"></div>
  </main>
  <footer>Generated {generated} UTC. Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>. Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.</footer>
<script>
const MIN_EMAILS = {TRACKED_SENDERS_MIN_EMAILS};
let tracker;
const esc = value => String(value).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function selectedRows() {{
  const rows = tracker.people[person.value].weekly;
  const weeks = [...new Set(rows.map(r => r.week))].sort();
  const count = Number(period.value);
  const allowed = new Set(count ? weeks.slice(-count) : weeks);
  return {{ rows: rows.filter(r => allowed.has(r.week)), weeks: [...allowed].sort() }};
}}
function renderChart(rows, weeks) {{
  const totals = Object.fromEntries(weeks.map(w => [w, [0, 0]]));
  rows.forEach(r => {{ totals[r.week][0] += r.matching_emails; totals[r.week][1] += r.total_emails; }});
  const values = weeks.map(w => totals[w][1] ? 100 * totals[w][0] / totals[w][1] : 0);
  const svg = document.getElementById('chart');
  if (!weeks.length) {{ svg.innerHTML = '<text x="400" y="140" text-anchor="middle">No qualifying emails in this period.</text>'; return; }}
  const left = 45, top = 20, width = 730, height = 210;
  const max = Math.max(5, Math.ceil(Math.max(...values) / 5) * 5);
  const xAt = i => left + (weeks.length === 1 ? width / 2 : i * width / (weeks.length - 1));
  const points = values.map((v, i) => `${{xAt(i)}},${{top + height - (v / max) * height}}`).join(' ');
  // ~1 label per 16 weeks, at least 6 (the 52-week view) and at most 10 so the
  // long all-time span gets denser labels without crowding.
  const tickCount = Math.min(weeks.length, Math.max(6, Math.min(10, Math.round(weeks.length / 16))));
  const tickIdx = [...new Set(Array.from({{length: tickCount}}, (_, k) =>
    tickCount === 1 ? 0 : Math.round(k * (weeks.length - 1) / (tickCount - 1))))];
  const MONTHS = ['Jan.', 'Feb.', 'Mar.', 'Apr.', 'May', 'Jun.', 'Jul.', 'Aug.', 'Sep.', 'Oct.', 'Nov.', 'Dec.'];
  const fmtWeek = w => {{ const p = w.split('-'); return `${{MONTHS[+p[1] - 1]}} ${{p[0]}}`; }};
  const xLabels = tickIdx.map(i => {{
    const x = xAt(i), y = top + height + 15;
    return `<line x1="${{x}}" y1="${{top + height}}" x2="${{x}}" y2="${{top + height + 4}}" stroke="#aaa"/><text x="${{x}}" y="${{y}}" text-anchor="end" transform="rotate(-30 ${{x}} ${{y}})" font-size="11">${{fmtWeek(weeks[i])}}</text>`;
  }}).join('');
  svg.innerHTML = `<line x1="${{left}}" y1="${{top + height}}" x2="${{left + width}}" y2="${{top + height}}" stroke="#aaa"/><line x1="${{left}}" y1="${{top}}" x2="${{left}}" y2="${{top + height}}" stroke="#aaa"/><polyline points="${{points}}" fill="none" stroke="{ACCENT}" stroke-width="3"/><text x="${{left}}" y="15" font-size="12">% of qualifying emails mentioning this person</text><text x="5" y="${{top + 5}}" font-size="11">${{max}}%</text><text x="10" y="${{top + height}}" font-size="11">0%</text>${{xLabels}}`;
}}
function render() {{
  const {{rows, weeks}} = selectedRows();
  const byCommittee = new Map();
  rows.forEach(r => {{ const x = byCommittee.get(r.committee) || {{total: 0, matching: 0, party: r.party}}; x.total += r.total_emails; x.matching += r.matching_emails; byCommittee.set(r.committee, x); }});
  const visible = [...byCommittee.entries()].filter(([, x]) => x.total >= MIN_EMAILS).sort((a, b) => b[1].matching / b[1].total - a[1].matching / a[1].total || b[1].total - a[1].total);
  const total = rows.reduce((n, r) => n + r.total_emails, 0), matching = rows.reduce((n, r) => n + r.matching_emails, 0);
  summary.textContent = `${{matching.toLocaleString()}} of ${{total.toLocaleString()}} qualifying emails (${{total ? (100 * matching / total).toFixed(1) : '0.0'}}%) mentioned ${{tracker.people[person.value].name}} across ${{visible.length}} displayed committees.`;
  renderChart(rows, weeks);
  table.innerHTML = visible.length ? `<table class="mention-table"><thead><tr><th>Committee</th><th>Party</th><th class="num">Matching</th><th class="num">Emails</th><th class="num">Rate</th></tr></thead><tbody>${{visible.map(([name, x]) => `<tr><td>${{esc(name)}}</td><td class="party">${{esc(x.party)}}</td><td class="num">${{x.matching.toLocaleString()}}</td><td class="num">${{x.total.toLocaleString()}}</td><td class="num">${{(100 * x.matching / x.total).toFixed(1)}}%</td></tr>`).join('')}}</tbody></table>` : '<p>No committees met the minimum email threshold in this period.</p>';
}}
fetch('sender_mentions.json').then(r => r.ok ? r.json() : Promise.reject()).then(data => {{ tracker = data; Object.entries(data.people).forEach(([slug, p]) => person.add(new Option(p.name, slug))); person.addEventListener('change', render); period.addEventListener('change', render); render(); }}).catch(() => {{ summary.innerHTML = '<span class="tracker-error">The tracking data could not be loaded.</span>'; }});
</script>
</body>
</html>"""


_COMMITTEES_PAGE_CSS = """
    .tracker-intro { color: #555; margin-bottom: 1rem; }
    .tracker-controls { display: flex; flex-wrap: wrap; gap: 1rem; align-items: end; margin: 1rem 0; }
    .tracker-controls label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.85rem; font-weight: 600; }
    .tracker-controls select, .tracker-controls input { font: inherit; padding: 0.35rem 0.5rem; border: 1px solid var(--border); border-radius: 4px; background: white; }
    .tracker-controls input { min-width: 14rem; }
    .tracker-summary { color: #666; font-size: 0.9rem; margin: 0.5rem 0 1rem; }
    .tracker-chart { width: 100%; min-height: 280px; background: white; border: 1px solid var(--border); border-radius: 4px; padding: 0.5rem; margin-bottom: 1rem; }
    .mention-table { width: 100%; border-collapse: collapse; background: white; font-size: 0.9rem; }
    .mention-table th, .mention-table td { padding: 0.55rem 0.65rem; border-bottom: 1px solid var(--border); text-align: left; }
    .mention-table th { color: var(--primary); font-size: 0.75rem; letter-spacing: 0.05em; text-transform: uppercase; }
    .mention-table th.sortable { cursor: pointer; user-select: none; }
    .mention-table th.sortable:hover { text-decoration: underline; }
    .mention-table td.num, .mention-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .mention-table .party { font-weight: 700; }
    .show-all { margin: 1rem 0; font: inherit; padding: 0.4rem 0.9rem; border: 1px solid var(--border); border-radius: 4px; background: white; cursor: pointer; }
    .tracker-error { color: #8b1e1e; }
    .domains-link { margin-left: 0.5rem; font-size: 0.8rem; white-space: nowrap; }
    .domains-row > td { background: #f7f9f7; padding: 0.5rem 0.65rem 0.9rem 2rem; }
    .domain-table { border-collapse: collapse; font-size: 0.85rem; background: white; }
    .domain-table th, .domain-table td { padding: 0.3rem 0.8rem; border-bottom: 1px solid var(--border); text-align: left; }
    .domain-table th { font-size: 0.7rem; letter-spacing: 0.05em; text-transform: uppercase; color: var(--primary); }
    .domain-table .num { text-align: right; font-variant-numeric: tabular-nums; }
"""

_COMMITTEES_PAGE_BODY = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Committees — Political Email Archive</title>
  <style>__CSS__</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>Which committees send the most political email, and how concentrated it is.</p>
    <div class="header-links"><a href="index.html">Home</a><a href="downloads.html">All Downloads</a><a href="sender-mentions.html">Sender mentions</a><a href="https://github.com/dwillis/political-emails">GitHub</a></div>
  </header>
  <main>
    <h2>Committees</h2>
    <p class="tracker-intro">Counts every email with an identified committee, with or without a campaign disclaimer. Committees are grouped by FEC ID where known, otherwise by normalized name. Committees need at least __MIN__ emails in the selected period to appear in the table.</p>
    <p class="tracker-intro"><a href="committees.json">Download the complete monthly data (JSON)</a></p>
    <div class="tracker-controls">
      <label>Period <select id="period"></select></label>
      <label>Party <select id="party"><option value="all">All</option><option value="D">Democratic</option><option value="R">Republican</option><option value="OTH">Other (I/G)</option><option value="unknown">Unknown</option></select></label>
      <label>Search <input id="search" type="search" placeholder="Committee name or FEC ID"></label>
    </div>
    <p class="tracker-summary" id="summary">Loading…</p>
    <svg class="tracker-chart" id="conc-chart" viewBox="0 0 800 280" role="img" aria-label="Cumulative share of email by committee rank"></svg>
    <svg class="tracker-chart" id="new-chart" viewBox="0 0 800 280" role="img" aria-label="New committees over time"></svg>
    <div id="table"></div>
  </main>
  <footer>Generated __GENERATED__ UTC. Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>. Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.</footer>
<script>
const MIN_EMAILS = __MIN__;
const ROW_CAP = 500;
const ACCENT = '__ACCENT__';
const PARTY_NAMES = {D: 'D', R: 'R', OTH: 'Other', unknown: '—'};
let data;
let sortKey = 'total', sortDir = -1, showAll = false;
let domainData = null, domainLoad = null;
const openDomains = new Set();
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const MONTHS = ['Jan.', 'Feb.', 'Mar.', 'Apr.', 'May', 'Jun.', 'Jul.', 'Aug.', 'Sep.', 'Oct.', 'Nov.', 'Dec.'];
const fmtMonth = m => { const p = m.split('-'); return MONTHS[+p[1] - 1] + ' ' + p[0]; };

function monthMatcher() {
  const v = period.value;
  if (v === 'all') return () => true;
  if (v === '12') {
    const last = data.months[data.months.length - 1].split('-');
    let y = +last[0], m = +last[1] - 11;
    if (m < 1) { m += 12; y -= 1; }
    const cutoff = y + '-' + String(m).padStart(2, '0');
    return mo => mo >= cutoff;
  }
  return mo => mo.slice(0, 4) === v;
}

function compute() {
  const inPeriod = monthMatcher();
  const wantParty = party.value;
  const totals = new Map();
  data.monthly.forEach(([mo, i, n]) => {
    if (!inPeriod(mo)) return;
    if (wantParty !== 'all' && data.committees[i].party !== wantParty) return;
    totals.set(i, (totals.get(i) || 0) + n);
  });
  return { inPeriod, totals, wantParty };
}

function renderConcentration(totals) {
  const svg = document.getElementById('conc-chart');
  const values = [...totals.values()].sort((a, b) => b - a);
  if (!values.length) { svg.innerHTML = '<text x="400" y="140" text-anchor="middle">No emails in this period.</text>'; return; }
  const grand = values.reduce((a, b) => a + b, 0);
  const left = 45, top = 25, width = 730, height = 195;
  const n = values.length;
  const xAt = rank => left + (n === 1 ? width / 2 : width * Math.log(rank) / Math.log(n));
  let run = 0, half = 0;
  const pts = values.map((v, i) => { run += v; if (!half && run * 2 >= grand) half = i + 1; return xAt(i + 1) + ',' + (top + height - height * run / grand); });
  const ticks = [1, 10, 100, 1000, 10000].filter(t => t <= n);
  if (ticks[ticks.length - 1] !== n) ticks.push(n);
  const xTicks = ticks.map(t => '<line x1="' + xAt(t) + '" y1="' + (top + height) + '" x2="' + xAt(t) + '" y2="' + (top + height + 4) + '" stroke="#aaa"/><text x="' + xAt(t) + '" y="' + (top + height + 17) + '" text-anchor="middle" font-size="11">' + t.toLocaleString() + '</text>').join('');
  const hx = xAt(half), hy = top + height - height / 2;
  const label = 'Top ' + half.toLocaleString() + ' of ' + n.toLocaleString() + ' committees send half of all email';
  const anchor = hx > left + width * 0.6 ? 'end' : 'start';
  const lx = anchor === 'end' ? hx - 8 : hx + 8;
  svg.innerHTML = '<line x1="' + left + '" y1="' + (top + height) + '" x2="' + (left + width) + '" y2="' + (top + height) + '" stroke="#aaa"/><line x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + (top + height) + '" stroke="#aaa"/>'
    + '<line x1="' + left + '" y1="' + hy + '" x2="' + hx + '" y2="' + hy + '" stroke="#ccc" stroke-dasharray="4 3"/>'
    + '<polyline points="' + pts.join(' ') + '" fill="none" stroke="' + ACCENT + '" stroke-width="3"/>'
    + '<circle cx="' + hx + '" cy="' + hy + '" r="4" fill="#1e4d2b"/><text x="' + lx + '" y="' + (hy + 18) + '" text-anchor="' + anchor + '" font-size="12">' + esc(label) + '</text>'
    + '<text x="' + left + '" y="15" font-size="12">Cumulative share of emails, committees ranked by volume (log scale)</text>'
    + '<text x="5" y="' + (top + 5) + '" font-size="11">100%</text><text x="14" y="' + (top + height) + '" font-size="11">0%</text>' + xTicks
    + '<text x="' + (left + width / 2) + '" y="' + (top + height + 33) + '" text-anchor="middle" font-size="11">Committee rank</text>';
}

function renderNew(inPeriod) {
  const svg = document.getElementById('new-chart');
  const counts = new Map();
  const yearly = period.value === 'all';
  data.committees.forEach(c => {
    if (party.value !== 'all' && c.party !== party.value) return;
    const mo = c.first_seen.slice(0, 7);
    if (!inPeriod(mo)) return;
    const k = yearly ? mo.slice(0, 4) : mo;
    counts.set(k, (counts.get(k) || 0) + 1);
  });
  const keys = [...new Set(data.months.filter(inPeriod).map(m => yearly ? m.slice(0, 4) : m))].sort();
  if (!keys.length) { svg.innerHTML = '<text x="400" y="140" text-anchor="middle">No data in this period.</text>'; return; }
  const left = 45, top = 25, width = 730, height = 195;
  const max = Math.max(5, ...keys.map(k => counts.get(k) || 0));
  const niceMax = Math.ceil(max / 5) * 5;
  const slot = width / keys.length, bw = Math.max(1, slot * 0.7);
  const every = Math.max(1, Math.ceil(keys.length / 12));
  const bars = keys.map((k, i) => {
    const v = counts.get(k) || 0, h = height * v / niceMax, x = left + i * slot + (slot - bw) / 2;
    const lab = i % every === 0 ? '<text x="' + (x + bw / 2) + '" y="' + (top + height + 15) + '" text-anchor="end" transform="rotate(-30 ' + (x + bw / 2) + ' ' + (top + height + 15) + ')" font-size="11">' + (yearly ? k : fmtMonth(k)) + '</text>' : '';
    return '<rect x="' + x + '" y="' + (top + height - h) + '" width="' + bw + '" height="' + h + '" fill="' + ACCENT + '"><title>' + (yearly ? k : fmtMonth(k)) + ': ' + v.toLocaleString() + ' new</title></rect>' + lab;
  }).join('');
  svg.innerHTML = '<line x1="' + left + '" y1="' + (top + height) + '" x2="' + (left + width) + '" y2="' + (top + height) + '" stroke="#aaa"/><line x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + (top + height) + '" stroke="#aaa"/>'
    + bars + '<text x="' + left + '" y="15" font-size="12">New committees per ' + (yearly ? 'year' : 'month') + ' (first email seen)</text>'
    + '<text x="5" y="' + (top + 5) + '" font-size="11">' + niceMax.toLocaleString() + '</text><text x="30" y="' + (top + height) + '" font-size="11">0</text>';
}

function dominantSource(sources) {
  return Object.entries(sources).sort((a, b) => b[1] - a[1])[0][0];
}

function domainRow(i) {
  const rows = domainData && domainData[i];
  let inner;
  if (!domainData) inner = 'Loading domains…';
  else if (!rows || !rows.length) inner = 'No sender domains recorded.';
  else inner = '<table class="domain-table"><thead><tr><th>Domain</th><th class="num">Emails</th><th>First seen</th><th>Last seen</th></tr></thead><tbody>'
    + rows.map(r => '<tr><td>' + esc(r[0]) + '</td><td class="num">' + r[1].toLocaleString() + '</td><td>' + esc(r[2]) + '</td><td>' + esc(r[3]) + '</td></tr>').join('') + '</tbody></table>';
  return '<tr class="domains-row"><td colspan="7">' + inner + '</td></tr>';
}

function loadDomains() {
  if (!domainLoad) domainLoad = fetch('committee_domains.json').then(r => r.ok ? r.json() : Promise.reject()).then(d => { domainData = d; render(); }).catch(() => { domainLoad = null; openDomains.clear(); summary.innerHTML = '<span class="tracker-error">The domain data could not be loaded.</span>'; });
}

function renderTable(totals) {
  const grand = [...totals.values()].reduce((a, b) => a + b, 0);
  const q = search.value.trim().toLowerCase();
  let rows = [...totals.entries()].filter(([, n]) => n >= MIN_EMAILS).map(([i, n]) => ({ c: data.committees[i], n }));
  const hidden = totals.size - rows.length;
  if (q) rows = rows.filter(r => r.c.name.toLowerCase().includes(q) || (r.c.fec_id || '').toLowerCase().includes(q));
  const keyFn = { total: r => r.n, name: r => r.c.name.toLowerCase(), first_seen: r => r.c.first_seen, last_seen: r => r.c.last_seen }[sortKey];
  rows.sort((a, b) => { const x = keyFn(a), y = keyFn(b); return (x < y ? -1 : x > y ? 1 : 0) * sortDir || b.n - a.n; });
  summary.textContent = grand.toLocaleString() + ' emails from ' + totals.size.toLocaleString() + ' committees in this period; ' + (totals.size - hidden).toLocaleString() + ' have at least ' + MIN_EMAILS + ' emails' + (q ? ' (' + rows.length.toLocaleString() + ' match the search)' : '') + '.';
  const shown = showAll ? rows : rows.slice(0, ROW_CAP);
  const th = (key, label, cls) => '<th class="sortable ' + (cls || '') + '" data-sort="' + key + '">' + label + (sortKey === key ? (sortDir < 0 ? ' ▼' : ' ▲') : '') + '</th>';
  if (!rows.length) { table.innerHTML = '<p>No committees met the minimum email threshold in this period.</p>'; return; }
  const body = shown.map(({ c, n }) => {
    const name = c.fec_id ? '<a href="https://www.fec.gov/data/committee/' + encodeURIComponent(c.fec_id) + '/">' + esc(c.name) + '</a>' : esc(c.name);
    const mix = Object.entries(c.sources).map(([k, v]) => k + ': ' + v.toLocaleString()).join(', ');
    const open = openDomains.has(data.committees.indexOf(c));
    const link = '<a href="#" class="domains-link" data-domains="' + data.committees.indexOf(c) + '">' + (open ? 'Hide domains' : 'Domains') + '</a>';
    return '<tr><td>' + name + link + '</td><td class="party">' + esc(PARTY_NAMES[c.party] || c.party) + '</td><td class="num">' + n.toLocaleString() + '</td><td class="num">' + (100 * n / grand).toFixed(2) + '%</td><td>' + esc(c.first_seen) + '</td><td>' + esc(c.last_seen) + '</td><td title="' + esc(mix) + '">' + esc(dominantSource(c.sources)) + '</td></tr>' + (open ? domainRow(data.committees.indexOf(c)) : '');
  }).join('');
  const more = !showAll && rows.length > ROW_CAP ? '<button class="show-all" id="show-all">Show all ' + rows.length.toLocaleString() + ' committees</button>' : '';
  table.innerHTML = '<table class="mention-table"><thead><tr>' + th('name', 'Committee') + '<th>Party</th>' + th('total', 'Emails', 'num') + '<th class="num">Share</th>' + th('first_seen', 'First seen') + th('last_seen', 'Last seen') + '<th>Source</th></tr></thead><tbody>' + body + '</tbody></table>' + more;
}

function render() {
  const { inPeriod, totals } = compute();
  renderConcentration(totals);
  renderNew(inPeriod);
  renderTable(totals);
}

fetch('committees.json').then(r => r.ok ? r.json() : Promise.reject()).then(d => {
  data = d;
  data.months = [...new Set(d.monthly.map(r => r[0]))].sort();
  const years = [...new Set(data.months.map(m => m.slice(0, 4)))].sort().reverse();
  period.add(new Option('Last 12 months', '12'));
  years.forEach(y => period.add(new Option(y, y)));
  period.add(new Option('All time', 'all'));
  period.value = 'all';
  period.addEventListener('change', () => { showAll = false; render(); });
  party.addEventListener('change', () => { showAll = false; render(); });
  search.addEventListener('input', () => { showAll = false; render(); });
  table.addEventListener('click', e => {
    if (e.target.id === 'show-all') { showAll = true; render(); return; }
    const di = e.target.dataset && e.target.dataset.domains;
    if (di !== undefined) {
      e.preventDefault();
      const i = +di;
      if (openDomains.has(i)) openDomains.delete(i); else { openDomains.add(i); loadDomains(); }
      render();
      return;
    }
    const key = e.target.dataset && e.target.dataset.sort;
    if (!key) return;
    if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = key === 'name' ? 1 : -1; }
    render();
  });
  render();
}).catch(() => { summary.innerHTML = '<span class="tracker-error">The committee data could not be loaded.</span>'; });
</script>
</body>
</html>"""


def generate_committees_html(generated_iso):
    """Generate the client-rendered committees page (committees.html)."""
    return (
        _COMMITTEES_PAGE_BODY
        .replace("__CSS__", SHARED_CSS + _COMMITTEES_PAGE_CSS)
        .replace("__GENERATED__", escape(str(generated_iso)[:16].replace("T", " ")))
        .replace("__MIN__", str(COMMITTEES_MIN_EMAILS))
        .replace("__ACCENT__", ACCENT)
    )


_DOMAINS_PAGE_CSS = """
    .tracker-intro { color: #555; margin-bottom: 1rem; }
    .tracker-controls { display: flex; flex-wrap: wrap; gap: 1rem; align-items: end; margin: 1rem 0; }
    .tracker-controls[hidden] { display: none; }
    .tracker-controls label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.85rem; font-weight: 600; }
    .search-wrap { position: relative; }
    .tracker-controls input { font: inherit; padding: 0.35rem 0.5rem; border: 1px solid var(--border); border-radius: 4px; background: white; min-width: 16rem; }
    .tracker-summary { color: #666; font-size: 0.9rem; margin: 0.5rem 0 1rem; }
    .suggest { display: none; position: absolute; top: 100%; left: 0; width: 100%; max-width: 40rem; z-index: 20; background: white; border: 1px solid var(--border); border-radius: 4px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); max-height: 24rem; overflow-y: auto; }
    .suggest-item { padding: 0.4rem 0.7rem; font-size: 0.85rem; cursor: pointer; }
    .suggest-item:hover { background: #f2f6f2; }
    .suggest-meta { display: block; color: #777; font-size: 0.75rem; }
    .suggest-empty { padding: 0.4rem 0.7rem; font-size: 0.85rem; color: #777; }
    .mention-table { width: 100%; border-collapse: collapse; background: white; font-size: 0.9rem; }
    .mention-table th, .mention-table td { padding: 0.55rem 0.65rem; border-bottom: 1px solid var(--border); text-align: left; }
    .mention-table th { color: var(--primary); font-size: 0.75rem; letter-spacing: 0.05em; text-transform: uppercase; }
    .mention-table th.sortable { cursor: pointer; user-select: none; }
    .mention-table th.sortable:hover { text-decoration: underline; }
    .mention-table td.num, .mention-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .mention-table .party { font-weight: 700; }
    .back-row { margin: 0 0 0.5rem; }
    .back-row a { color: var(--primary); font-size: 0.9rem; }
    .rank { color: #888; font-variant-numeric: tabular-nums; }
    .tracker-error { color: #8b1e1e; }
"""

_DOMAINS_PAGE_BODY = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Domains — Political Email Archive</title>
  <style>__CSS__</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>The reverse of the committees page: choose a sender domain and see every identified committee that has sent from it.</p>
    <div class="header-links"><a href="index.html">Home</a><a href="downloads.html">All Downloads</a><a href="committees.html">Committees</a><a href="https://github.com/dwillis/political-emails">GitHub</a></div>
  </header>
  <main>
    <h2 id="viewTitle">Top 50 domains</h2>
    <p class="back-row" id="backRow" hidden><a href="#" id="back-link">← All domains</a></p>
    <p class="tracker-intro">Counts cover committee-identified emails only, all time — the same email universe as the <a href="committees.html">committees page</a>. They will not match the dashboard's "Top 10 sender domains" chart, which counts every email, committee or not.</p>
    <p class="tracker-intro"><a href="committee_domains.json">Download the underlying per-committee data (JSON)</a></p>
    <div class="tracker-controls" id="controls"><label>Search <div class="search-wrap"><input id="search" type="search" placeholder="e.g. win.donaldjtrump.com" autocomplete="off"><div class="suggest" id="suggest"></div></div></label></div>
    <p class="tracker-summary" id="summary">Loading…</p>
    <div id="table"></div>
  </main>
  <footer>Generated __GENERATED__ UTC. Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>. Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.</footer>
<script>
const PARTY_NAMES = {D: 'D', R: 'R', OTH: 'Other', unknown: '—'};
const byDomain = new Map();   // domain -> [[cIdx, emails, first, last], ...] (cIdx indexes into data.committees)
let domains = [];             // [domain, total_emails, first_seen, last_seen, committee_count] sorted by emails desc
let data;
let selected = null;          // selected domain, or null for the top-50 view
let sortKey = 'total', sortDir = -1;   // reset on every view change
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function extremes(rows) {
  let total = 0, first = '9999-99-99', last = '';
  rows.forEach(r => { total += r[1]; if (r[2] < first) first = r[2]; if (r[3] > last) last = r[3]; });
  return { total, first, last };
}

const TOP_N = 50;
const SUGGEST_MAX = 15;

function th(key, label, cls) {
  return '<th class="sortable ' + (cls || '') + '" data-sort="' + key + '">' + label
    + (sortKey === key ? (sortDir < 0 ? ' ▼' : ' ▲') : '') + '</th>';
}

function renderTop() {
  viewTitle.textContent = 'Top 50 domains';
  backRow.hidden = true; controls.hidden = false;
  if (!domains.length) { summary.textContent = 'No committee-linked emails recorded yet.'; table.innerHTML = ''; return; }
  const grand = domains.reduce((s, d) => s + d[1], 0);
  summary.textContent = grand.toLocaleString() + ' committee-identified emails across '
    + domains.length.toLocaleString() + ' sender domains from ' + data.committees.length.toLocaleString()
    + ' committees. The default list ranks the ' + TOP_N + ' highest-volume domains; search covers every domain.';
  const keyFns = { domain: d => d[0], committees: d => d[4], total: d => d[1], first_seen: d => d[2], last_seen: d => d[3] };
  const sorted = domains.slice().sort((a, b) => {
    const x = keyFns[sortKey](a), y = keyFns[sortKey](b);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir || b[1] - a[1];
  });
  const body = sorted.slice(0, TOP_N).map((d, i) =>
    '<tr><td class="rank">' + (i + 1) + '</td><td><a href="#" class="domain-link" data-domain="' + esc(d[0]) + '">' + esc(d[0]) + '</a></td>'
    + '<td class="num">' + d[4].toLocaleString() + '</td><td class="num">' + d[1].toLocaleString() + '</td>'
    + '<td>' + esc(d[2]) + '</td><td>' + esc(d[3]) + '</td></tr>'
  ).join('');
  table.innerHTML = '<table class="mention-table"><thead><tr><th>Rank</th>' + th('domain', 'Domain')
    + th('committees', 'Committees', 'num') + th('total', 'Emails', 'num')
    + th('first_seen', 'First seen') + th('last_seen', 'Last seen')
    + '</tr></thead><tbody>' + body + '</tbody></table>';
}

function render() {
  if (selected) renderDomain(); else renderTop();
}

function renderDomain() {
  viewTitle.textContent = selected;
  backRow.hidden = false; controls.hidden = true;
  const rows = selected ? byDomain.get(selected.toLowerCase()) : null;
  if (!rows || !rows.length) {
    summary.innerHTML = 'No committee-linked emails found for <strong>' + esc(selected) + '</strong>.';
    table.innerHTML = '';
    return;
  }
  const x = extremes(rows);
  summary.textContent = x.total.toLocaleString() + ' committee-identified emails from '
    + rows.length.toLocaleString() + (rows.length === 1 ? ' committee' : ' committees')
    + ', first seen ' + x.first + ', last seen ' + x.last + '.';
  const keyFns = { name: r => r.c.name.toLowerCase(), total: r => r.n, pct: r => r.n / r.c.total, first_seen: r => r.first, last_seen: r => r.last };
  const shown = rows.map(r => ({ c: data.committees[r[0]], n: r[1], first: r[2], last: r[3] }))
    .sort((a, b) => {
      const x1 = keyFns[sortKey](a), y1 = keyFns[sortKey](b);
      return (x1 < y1 ? -1 : x1 > y1 ? 1 : 0) * sortDir || b.n - a.n;
    });
  const body = shown.map(r => {
    const name = r.c.fec_id
      ? '<a href="https://www.fec.gov/data/committee/' + encodeURIComponent(r.c.fec_id) + '/">' + esc(r.c.name) + '</a>'
      : esc(r.c.name);
    const pct = r.c.total ? 100 * r.n / r.c.total : 0;
    return '<tr><td>' + name + '</td><td class="party">' + esc(PARTY_NAMES[r.c.party] || r.c.party) + '</td>'
      + '<td class="num">' + r.n.toLocaleString() + '</td><td class="num">' + pct.toFixed(1) + '%</td>'
      + '<td>' + esc(r.first) + '</td><td>' + esc(r.last) + '</td></tr>';
  }).join('');
  table.innerHTML = '<table class="mention-table"><thead><tr>' + th('name', 'Committee') + '<th>Party</th>'
    + th('total', 'Emails', 'num') + th('pct', "% of committee's total", 'num')
    + th('first_seen', 'First seen') + th('last_seen', 'Last seen')
    + '</tr></thead><tbody>' + body + '</tbody></table>';
}

function setUrlParam(domain) {
  const url = new URL(location.href);
  if (domain) url.searchParams.set('d', domain); else url.searchParams.delete('d');
  history.replaceState(null, '', url);
}

function selectDomain(domain) {
  selected = domain; sortKey = 'total'; sortDir = -1;
  setUrlParam(domain);
  search.value = ''; hideSuggest();
  render(); window.scrollTo(0, 0);
}

function deselect() {
  selected = null; sortKey = 'total'; sortDir = -1;
  setUrlParam(null);
  render(); window.scrollTo(0, 0);
}

function hideSuggest() { suggest.style.display = 'none'; suggest.innerHTML = ''; }

function showSuggest(q) {
  if (!domains.length || !q) { hideSuggest(); return; }
  const matches = domains.filter(d => d[0] === q)
    .concat(domains.filter(d => d[0] !== q && d[0].indexOf(q) === 0))
    .concat(domains.filter(d => d[0] !== q && d[0].indexOf(q) > 0))
    .slice(0, SUGGEST_MAX);
  suggest.innerHTML = matches.length ? matches.map(d =>
    '<div class="suggest-item" data-domain="' + esc(d[0]) + '">' + esc(d[0])
    + '<span class="suggest-meta">' + d[1].toLocaleString() + ' emails · ' + d[4].toLocaleString() + ' committees</span></div>'
  ).join('') : '<div class="suggest-empty">No matching domains.</div>';
  suggest.style.display = 'block';
}

search.addEventListener('input', () => showSuggest(search.value.trim().toLowerCase()));
search.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const first = suggest.querySelector('.suggest-item');
    if (first) selectDomain(first.dataset.domain);
  } else if (e.key === 'Escape') {
    hideSuggest();
  }
});
suggest.addEventListener('mousedown', e => {
  const item = e.target.closest && e.target.closest('.suggest-item');
  if (item) { e.preventDefault(); selectDomain(item.dataset.domain); }
});
document.addEventListener('click', e => {
  if (!e.target.closest || !e.target.closest('.search-wrap')) hideSuggest();
});
backRow.addEventListener('click', e => {
  if (e.target.id === 'back-link') { e.preventDefault(); deselect(); }
});
table.addEventListener('click', e => {
  if (e.target.dataset && e.target.dataset.domain) { e.preventDefault(); selectDomain(e.target.dataset.domain); return; }
  const key = e.target.dataset && e.target.dataset.sort;
  if (!key) return;
  if (sortKey === key) sortDir = -sortDir;
  else { sortKey = key; sortDir = (key === 'domain' || key === 'name') ? 1 : -1; }
  render();
});

Promise.all([
  fetch('committees.json').then(r => r.ok ? r.json() : Promise.reject()),
  fetch('committee_domains.json').then(r => r.ok ? r.json() : Promise.reject()),
]).then(([committees, perCommittee]) => {
  data = committees;
  perCommittee.forEach((rows, cIdx) => rows.forEach(row => {
    const list = byDomain.get(row[0]);
    if (list) list.push([cIdx, row[1], row[2], row[3]]);
    else byDomain.set(row[0], [[cIdx, row[1], row[2], row[3]]]);
  }));
  domains = [...byDomain.entries()].map(([domain, rows]) => {
    const x = extremes(rows);
    return [domain, x.total, x.first, x.last, rows.length];
  }).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  // Deep link: ?d=<domain> selects a domain on load (build lowercases domain keys).
  const want = new URL(location.href).searchParams.get('d');
  if (want) selected = want.toLowerCase();
  render();
}).catch(() => { summary.innerHTML = '<span class="tracker-error">The domain data could not be loaded.</span>'; });
</script>
</body>
</html>"""


def generate_domains_html(generated_iso):
    """Generate the client-rendered domains page (domains.html)."""
    return (
        _DOMAINS_PAGE_BODY
        .replace("__CSS__", SHARED_CSS + _DOMAINS_PAGE_CSS)
        .replace("__GENERATED__", escape(str(generated_iso)[:16].replace("T", " ")))
    )


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
          <span class="dl-meta">{info['zip_size']} &middot; {info['total_records']:,} records</span>
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
    <h1>Downloads — Political <span>Email</span> Archive</h1>
    <p>All monthly and yearly JSONL archives ({year_range}).</p>
    <div class="header-links">
      <a href="index.html">← Dashboard</a>
      <a href="committees.html">Committees</a>
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

    recent_summary = build_recent(hours=24)
    print(f"  Recent emails (24h): {recent_summary['count']:,}")

    keyword_daily = stats.get("keyword_daily") or {}
    (DOCS_DIR / "keyword_daily.json").write_text(
        json.dumps(keyword_daily, ensure_ascii=False)
    )
    kw_totals = {
        kw: sum(v for day in daily.values() for v in day.values())
        for kw, daily in keyword_daily.items()
    }
    print(f"  Keyword matches: {kw_totals}")

    sender_mentions = stats["sender_mentions"]
    sender_mentions["generated_at"] = datetime.now(timezone.utc).isoformat()
    sender_mentions["minimum_display_emails"] = TRACKED_SENDERS_MIN_EMAILS
    (DOCS_DIR / "sender_mentions.json").write_text(
        json.dumps(sender_mentions, ensure_ascii=False)
    )
    sender_page = DOCS_DIR / "sender-mentions.html"
    sender_page.write_text(generate_sender_mentions_html(sender_mentions["generated_at"]))
    print(f"  Wrote {sender_page}")

    committees = stats["committees"]
    committees["generated_at"] = sender_mentions["generated_at"]
    committees["minimum_display_emails"] = COMMITTEES_MIN_EMAILS
    (DOCS_DIR / "committee_domains.json").write_text(
        json.dumps(committees.pop("domains"), ensure_ascii=False, separators=(",", ":"))
    )
    (DOCS_DIR / "committees.json").write_text(
        json.dumps(committees, ensure_ascii=False, separators=(",", ":"))
    )
    committees_page = DOCS_DIR / "committees.html"
    committees_page.write_text(generate_committees_html(committees["generated_at"]))
    print(f"  Wrote {committees_page} ({len(committees['committees']):,} committees)")

    dash_path = DOCS_DIR / "index.html"
    dash_path.write_text(generate_dashboard_html(stats, download_info, recent_summary))
    print(f"  Wrote {dash_path}")

    dl_path = DOCS_DIR / "downloads.html"
    dl_path.write_text(generate_downloads_html(download_info))
    print(f"  Wrote {dl_path}")

    total_downloads = len(list(DOWNLOADS_DIR.iterdir()))
    print(f"  {total_downloads} download files in {DOWNLOADS_DIR}")
    print("Done.")


if __name__ == "__main__":
    main()
