#!/usr/bin/env python3
"""Incremental email collection via IMAP.

Connects to Gmail, fetches emails since the last watermark date,
processes them, and appends to daily JSONL files.

Usage:
    uv run python scripts/collect_emails.py
    uv run python scripts/collect_emails.py --since 2026-04-01
    uv run python scripts/collect_emails.py --dry-run
    uv run python scripts/collect_emails.py --folder "INBOX"

Environment variables:
    GMAIL_USER          Gmail address
    GMAIL_APP_PASSWORD  Gmail App Password (not regular password)
"""

import argparse
import email
import imaplib
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from process_email import process_single_email, content_key, load_domain_party_map
from utils import (
    CONFIG_DIR, day_path, load_day_message_ids, load_jsonl, load_watermark,
    save_jsonl, save_watermark,
)

BATCH_SIZE = 100


def connect_imap(user, password, folder='"[Gmail]/All Mail"'):
    """Connect to Gmail IMAP and select the specified folder."""
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(user, password)
    status, _ = mail.select(folder, readonly=True)
    if status != 'OK':
        raise RuntimeError(f"Failed to select folder {folder}: {status}")
    return mail


def fetch_uids_since(mail, since_date):
    """Search for message UIDs since the given date."""
    date_str = since_date.strftime("%d-%b-%Y")
    status, data = mail.uid('search', None, f'SINCE {date_str}')
    if status != 'OK':
        raise RuntimeError(f"IMAP search failed: {status}")
    uid_list = data[0].split()
    return uid_list


def fetch_message(mail, uid):
    """Fetch a single message by UID."""
    status, data = mail.uid('fetch', uid, '(RFC822)')
    if status != 'OK':
        return None
    raw_email = data[0][1]
    return email.message_from_bytes(raw_email)


def collect(since_date=None, dry_run=False, folder='"[Gmail]/All Mail"'):
    """Main collection loop."""
    user = os.environ.get('GMAIL_USER')
    password = os.environ.get('GMAIL_APP_PASSWORD')
    if not user or not password:
        print("Error: GMAIL_USER and GMAIL_APP_PASSWORD environment variables required",
              file=sys.stderr)
        sys.exit(1)

    domain_party_map = load_domain_party_map(CONFIG_DIR / "domain_party_mapping.csv")

    # Determine start date
    if since_date:
        start_date = since_date
    else:
        watermark = load_watermark()
        if watermark:
            # Overlap by 1 day for safety
            start_date = watermark - timedelta(days=1)
        else:
            # No watermark — fetch last 7 days as a safe default
            start_date = datetime.now() - timedelta(days=7)

    print(f"Fetching emails since {start_date.strftime('%Y-%m-%d')}", file=sys.stderr)

    # Connect
    mail = connect_imap(user, password, folder)
    uids = fetch_uids_since(mail, start_date)
    print(f"Found {len(uids)} messages to check", file=sys.stderr)

    if dry_run:
        print(f"Dry run — would process {len(uids)} messages", file=sys.stderr)
        mail.logout()
        return

    # Load message IDs we already have for dedup
    # We'll cache per-day sets as we encounter them
    day_id_cache = {}
    content_key_cache = set()

    # Buffer new records by (year, month, day)
    buffers = defaultdict(list)
    latest_date = None
    processed = 0
    skipped_dup = 0
    errors = 0

    # Process in batches
    for i in range(0, len(uids), BATCH_SIZE):
        batch = uids[i:i + BATCH_SIZE]
        for uid in batch:
            try:
                msg = fetch_message(mail, uid)
                if msg is None:
                    errors += 1
                    continue

                record = process_single_email(msg, domain_party_map)
                if record is None:
                    continue

                # Check dedup
                mid = record.get('message_id')
                year = record.get('year')
                month = record.get('month')
                day_num = record.get('day')

                if not (year and month and day_num):
                    continue

                # Load cached day IDs if not already loaded
                day_key = (year, month, day_num)
                if day_key not in day_id_cache:
                    path = day_path(year, month, day_num)
                    day_id_cache[day_key] = load_day_message_ids(path)

                if mid and mid in day_id_cache[day_key]:
                    skipped_dup += 1
                    continue

                # Content-level dedup
                ck = content_key(record)
                if ck in content_key_cache:
                    skipped_dup += 1
                    continue
                content_key_cache.add(ck)

                buffers[day_key].append(record)
                if mid:
                    day_id_cache[day_key].add(mid)

                # Track latest date
                if record.get('date'):
                    try:
                        dt = datetime.fromisoformat(record['date'])
                        if latest_date is None or dt > latest_date:
                            latest_date = dt
                    except (ValueError, TypeError):
                        pass

                processed += 1

            except Exception as e:
                print(f"Error processing UID {uid}: {e}", file=sys.stderr)
                errors += 1
                continue

        if (i + BATCH_SIZE) % 500 == 0:
            print(f"  ... checked {i + len(batch):,} of {len(uids):,}", file=sys.stderr)

    mail.logout()

    # Write buffers to disk
    files_updated = 0
    for day_key, records in buffers.items():
        if not records:
            continue
        year, month, day_num = day_key
        path = day_path(year, month, day_num)
        existing = load_jsonl(path)
        all_records = existing + records
        save_jsonl(path, all_records)
        files_updated += 1

    # Update watermark
    if latest_date:
        save_watermark(latest_date)

    print(f"\nCollection complete:", file=sys.stderr)
    print(f"  New emails: {processed:,}", file=sys.stderr)
    print(f"  Duplicates skipped: {skipped_dup:,}", file=sys.stderr)
    print(f"  Errors: {errors:,}", file=sys.stderr)
    print(f"  Files updated: {files_updated}", file=sys.stderr)
    if latest_date:
        print(f"  Watermark: {latest_date.isoformat()}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Collect political emails via IMAP")
    parser.add_argument("--since", type=str, default=None,
                        help="Override: fetch since this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count messages without processing")
    parser.add_argument("--folder", type=str, default='"[Gmail]/All Mail"',
                        help='IMAP folder (default: "[Gmail]/All Mail")')
    args = parser.parse_args()

    since_date = None
    if args.since:
        since_date = datetime.strptime(args.since, "%Y-%m-%d")

    collect(since_date=since_date, dry_run=args.dry_run, folder=args.folder)


if __name__ == "__main__":
    main()
