#!/usr/bin/env python3
"""Build public topic pages: recent screenshots per tracked keyword.

For each keyword in config/tracked_keywords.json, selects up to 3 recent emails
(with a campaign disclaimer) from the most recent day that has any, re-fetches
and screenshots them, and writes docs/topic/<keyword>/index.html plus the PNGs.

Runs once a day in the deploy workflow, after build_site.py. Resilient: any
per-keyword failure is logged and skipped, and the script always exits 0 so a
transient Gmail/render problem never fails the site deploy.

Usage:
    uv run python scripts/build_topic_pages.py

Environment variables:
    GMAIL_USER          Gmail address
    GMAIL_APP_PASSWORD  Gmail App Password
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from build_site import DOCS_DIR, generate_topic_html
from collect_emails import connect_imap
from screenshot_emails import iter_records, screenshot_email, select_targets
from utils import CONFIG_DIR

TOPIC_LIMIT = 3


def _record_day(rec):
    return str(rec.get("date", ""))[:10]


def pick_latest_day_targets(records, pattern, disclaimer=True, limit=TOPIC_LIMIT):
    """Select up to ``limit`` matching records from the most recent day.

    Matches ``pattern`` against subject + body (optionally requiring a
    disclaimer), finds the newest calendar day with any match, and returns
    ``(day, records)`` for that day sorted newest-first. Returns ``(None, [])``
    when nothing matches.
    """
    matches = select_targets(records, pattern, disclaimer=disclaimer, limit=0)
    if not matches:
        return None, []
    day = max(_record_day(r) for r in matches)
    same_day = [r for r in matches if _record_day(r) == day]
    same_day.sort(key=lambda r: str(r.get("date", "")), reverse=True)
    return day, same_day[:limit]


def load_tracked_keywords():
    return json.loads((CONFIG_DIR / "tracked_keywords.json").read_text())


def build_topic(mail, topic, pattern, records, now_iso):
    """Build one topic page. Returns the number of screenshots rendered."""
    day, targets = pick_latest_day_targets(records, pattern, disclaimer=True)
    out_dir = DOCS_DIR / "topic" / topic
    out_dir.mkdir(parents=True, exist_ok=True)

    emails = []
    for rec in targets:
        try:
            path = screenshot_email(mail, rec, out_dir)
        except Exception as e:
            print(f"  ! {topic}: failed {rec.get('unique_id')}: {e}", file=sys.stderr)
            path = None
        if path is None:
            continue
        emails.append({
            "unique_id": rec.get("unique_id"),
            "date": rec.get("date"),
            "name": rec.get("name"),
            "email": rec.get("email"),
            "party": rec.get("party"),
            "subject": rec.get("subject"),
            "image": path.name,
        })

    html = generate_topic_html(topic, day if emails else None, emails, now_iso)
    (out_dir / "index.html").write_text(html)
    print(f"  {topic}: {len(emails)} screenshot(s) from {day}", file=sys.stderr)
    return len(emails)


def main():
    import os

    now_iso = datetime.now(timezone.utc).isoformat()
    keywords = load_tracked_keywords()
    if not keywords:
        print("No tracked keywords; nothing to build.", file=sys.stderr)
        return

    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        print("Warning: GMAIL_USER / GMAIL_APP_PASSWORD not set; "
              "skipping topic pages.", file=sys.stderr)
        return

    records = list(iter_records())
    patterns = {kw: re.compile(pat, re.IGNORECASE) for kw, pat in keywords.items()}

    try:
        mail = connect_imap(user, password)
    except Exception as e:
        print(f"Warning: could not connect to Gmail ({e}); skipping topic pages.",
              file=sys.stderr)
        return

    try:
        for topic, pattern in patterns.items():
            try:
                build_topic(mail, topic, pattern, records, now_iso)
            except Exception as e:
                print(f"  ! {topic}: {e}", file=sys.stderr)
    finally:
        mail.logout()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never fail the deploy
        print(f"Topic page build error (ignored): {e}", file=sys.stderr)
    sys.exit(0)
