#!/usr/bin/env python3
"""Render faithful PNG screenshots of archived emails matching a keyword.

Re-fetches the raw message from Gmail by Message-ID (the archive stores only
cleaned text, not raw HTML), extracts the HTML body, and renders it with
headless Chromium via Playwright.

Usage:
    # Screenshot up to 25 datacenter emails (uses config/tracked_keywords.json)
    uv run python scripts/screenshot_emails.py --keyword datacenter

    # Any regex, a date range, one party, no limit
    uv run python scripts/screenshot_emails.py --pattern "climate" \\
        --since 2026-01-01 --until 2026-06-30 --party D --limit 0

Setup (one-time):
    uv sync --group screenshots
    uv run playwright install chromium

Environment variables:
    GMAIL_USER          Gmail address
    GMAIL_APP_PASSWORD  Gmail App Password (not the regular password)

Note: rendering loads remote images, so senders may register email "opens".
"""

import argparse
import email
import json
import os
import re
import sys
from datetime import date, timedelta
from html import escape
from pathlib import Path

from collect_emails import connect_imap
from process_email import extract_html
from utils import CONFIG_DIR, DATA_DIR

EMAIL_VIEWPORT_WIDTH = 600
DEFAULT_LIMIT = 25
# How long to let remote images settle before screenshotting anyway. Email
# tracking pixels and slow image hosts often never let the network go idle, so
# we give them a bounded window rather than waiting for full "networkidle".
NETWORK_SETTLE_MS = 6000
SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "screenshots"


def load_keyword_pattern(keyword):
    """Return a compiled regex for a keyword from config/tracked_keywords.json."""
    patterns = json.loads((CONFIG_DIR / "tracked_keywords.json").read_text())
    if keyword not in patterns:
        raise KeyError(
            f"Keyword {keyword!r} not in tracked_keywords.json "
            f"(have: {', '.join(patterns) or 'none'})"
        )
    return re.compile(patterns[keyword], re.IGNORECASE)


def resolve_date_range(year=None, since=None, until=None, all_years=False,
                       past_day=False, current_year=None, today=None):
    """Resolve the effective (since, until) date bounds for target selection.

    Precedence: explicit ``since``/``until`` win outright (each may stay open);
    then ``past_day`` (yesterday through ``today``); then ``all_years`` clears
    both bounds; otherwise a specific ``year`` (or, by default, ``current_year``)
    bounds the range to that calendar year.
    """
    if since is not None or until is not None:
        return since, until
    if past_day:
        yesterday = today - timedelta(days=1)
        return yesterday.isoformat(), today.isoformat()
    if all_years:
        return None, None
    target = year if year is not None else current_year
    return f"{target}-01-01", f"{target}-12-31"


def _record_date(rec):
    """Date portion (YYYY-MM-DD) of a record, or "" if absent."""
    return str(rec.get("date", ""))[:10]


def select_targets(records, pattern, since=None, until=None, party=None,
                   disclaimer=False, limit=DEFAULT_LIMIT):
    """Filter records to those worth screenshotting.

    Args:
        records: iterable of record dicts.
        pattern: compiled regex matched against subject + body.
        since, until: inclusive "YYYY-MM-DD" date bounds (optional).
        party: "D" / "R" filter (optional).
        disclaimer: if True, keep only records with a campaign disclaimer.
        limit: max targets; 0 means unlimited.

    Returns a list of the matching records, in input order, capped at ``limit``.
    Records without a message_id are skipped (can't be re-fetched).
    """
    hits = []
    for rec in records:
        if not rec.get("message_id"):
            continue
        if party and rec.get("party") != party:
            continue
        if disclaimer and not rec.get("disclaimer"):
            continue
        day = _record_date(rec)
        if since and day < since:
            continue
        if until and day > until:
            continue
        haystack = f"{rec.get('subject', '')} {rec.get('body', '')}"
        if not pattern.search(haystack):
            continue
        hits.append(rec)
        if limit and len(hits) >= limit:
            break
    return hits


def iter_records(data_dir=DATA_DIR):
    """Yield every record dict across all daily JSONL files, oldest first."""
    for year_dir in sorted(p for p in data_dir.iterdir() if p.is_dir() and p.name.isdigit()):
        for month_dir in sorted(p for p in year_dir.iterdir() if p.is_dir()):
            for f in sorted(month_dir.glob("*.jsonl")):
                with open(f) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue


def wrap_plaintext(text):
    """Wrap plain-text email content in a minimal HTML shell for rendering."""
    safe = escape(text or "")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        f"width:{EMAIL_VIEWPORT_WIDTH}px;padding:16px;white-space:pre-wrap;"
        "word-wrap:break-word;line-height:1.4}</style></head>"
        f"<body>{safe}</body></html>"
    )


def fetch_raw_message(mail, message_id):
    """Fetch a raw email.message by Message-ID from the selected IMAP folder.

    Returns an email.message.Message, or None if not found.
    """
    status, data = mail.uid("search", None, f'HEADER Message-ID "{message_id}"')
    if status != "OK" or not data or not data[0]:
        return None
    uid = data[0].split()[0]
    status, fetched = mail.uid("fetch", uid, "(RFC822)")
    if status != "OK" or not fetched or not fetched[0]:
        return None
    return email.message_from_bytes(fetched[0][1])


def render_html_to_png(html, out_path, width=EMAIL_VIEWPORT_WIDTH):
    """Render an HTML string to a full-page PNG at out_path using Playwright.

    Raises RuntimeError if the browser can't be launched (e.g. not installed).
    """
    from playwright.sync_api import sync_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 800})
            # Parse the DOM (fast), then give remote images a bounded window to
            # load. Waiting for full "networkidle" hangs on tracking pixels and
            # slow hosts, so settle failures are swallowed — we screenshot with
            # whatever loaded. Remote images may register "opens"; accepted.
            page.set_content(html, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_SETTLE_MS)
            except Exception:
                pass
            page.screenshot(path=str(out_path), full_page=True)
            browser.close()
    except Exception as e:
        raise RuntimeError(str(e)) from e


def screenshot_email(mail, rec, out_dir):
    """Fetch, extract, and render one record. Returns the output Path or None."""
    msg = fetch_raw_message(mail, rec["message_id"])
    if msg is None:
        print(f"  ! not found in mailbox: {rec['message_id']}", file=sys.stderr)
        return None
    html = extract_html(msg)
    if not html:
        _, body = None, rec.get("body", "")
        html = wrap_plaintext(body)
    out_path = Path(out_dir) / f"{_record_date(rec)}_{rec['unique_id']}.png"
    render_html_to_png(html, out_path)
    return out_path


def run(pattern, label, since=None, until=None, party=None, disclaimer=False,
        limit=DEFAULT_LIMIT, folder='"[Gmail]/All Mail"'):
    """Select matching records, fetch each from Gmail, and render PNGs."""
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        print("Error: GMAIL_USER and GMAIL_APP_PASSWORD environment variables required",
              file=sys.stderr)
        sys.exit(1)

    targets = select_targets(
        iter_records(), pattern, since=since, until=until, party=party,
        disclaimer=disclaimer, limit=limit,
    )
    if not targets:
        print("No matching emails found; nothing to screenshot.", file=sys.stderr)
        return
    print(f"Selected {len(targets)} email(s) to screenshot", file=sys.stderr)

    out_dir = SCREENSHOTS_DIR / label
    mail = connect_imap(user, password, folder)
    saved = 0
    try:
        for rec in targets:
            try:
                path = screenshot_email(mail, rec, out_dir)
                if path:
                    saved += 1
                    print(f"  saved {path}", file=sys.stderr)
            except Exception as e:
                print(f"  ! failed {rec.get('unique_id')}: {e}", file=sys.stderr)
    finally:
        mail.logout()
    print(f"Done. {saved} screenshot(s) in {out_dir}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Screenshot archived emails matching a keyword")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--keyword", help="Tracked keyword from config/tracked_keywords.json")
    group.add_argument("--pattern", help="Arbitrary regex to match (case-insensitive)")
    year_group = parser.add_mutually_exclusive_group()
    year_group.add_argument("--year", type=int,
                            help="Limit to a calendar year (default: current year)")
    year_group.add_argument("--all-years", action="store_true",
                            help="Do not restrict by year")
    year_group.add_argument("--past-day", action="store_true",
                            help="Only emails from the past day (yesterday and today)")
    parser.add_argument("--since", help="Inclusive start date YYYY-MM-DD (overrides --year)")
    parser.add_argument("--until", help="Inclusive end date YYYY-MM-DD (overrides --year)")
    parser.add_argument("--party", choices=["D", "R"], help="Filter by party")
    parser.add_argument("--disclaimer", action="store_true",
                        help="Only emails with a campaign disclaimer")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max emails (default {DEFAULT_LIMIT}; 0 = all)")
    parser.add_argument("--folder", default='"[Gmail]/All Mail"',
                        help='IMAP folder (default: "[Gmail]/All Mail")')
    args = parser.parse_args()

    if args.keyword:
        try:
            pattern = load_keyword_pattern(args.keyword)
        except KeyError as e:
            print(f"Error: {e.args[0]}", file=sys.stderr)
            sys.exit(1)
        label = args.keyword
    else:
        pattern = re.compile(args.pattern, re.IGNORECASE)
        label = "custom"

    today = date.today()
    since, until = resolve_date_range(
        year=args.year, since=args.since, until=args.until,
        all_years=args.all_years, past_day=args.past_day,
        current_year=today.year, today=today,
    )
    run(pattern, label, since=since, until=until, party=args.party,
        disclaimer=args.disclaimer, limit=args.limit, folder=args.folder)


if __name__ == "__main__":
    main()
