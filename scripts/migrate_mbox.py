#!/usr/bin/env python3
"""One-time migration: convert an MBOX file into daily JSONL files.

Usage:
    uv run python scripts/migrate_mbox.py
    uv run python scripts/migrate_mbox.py --mbox-path /path/to/file.mbox
    uv run python scripts/migrate_mbox.py --data-dir /path/to/data
"""

import argparse
import mailbox
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from process_email import process_single_email, load_domain_party_map
from utils import DATA_DIR, CONFIG_DIR, day_path, load_jsonl, save_jsonl, save_watermark

DEFAULT_MBOX_PATH = "/Volumes/LaCie2/ddhq/Takeout/Mail/All mail Including Spam and Trash.mbox"


def migrate(mbox_path, data_dir=None):
    if data_dir:
        # Override the global DATA_DIR for this run
        import utils
        utils.DATA_DIR = Path(data_dir)

    domain_party_map = load_domain_party_map(CONFIG_DIR / "domain_party_mapping.csv")

    # Wipe existing data for a clean migration
    data_dir = DATA_DIR if not data_dir else Path(data_dir)
    if data_dir.exists():
        print(f"Clearing existing data in {data_dir}", file=sys.stderr)
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Buffers keyed by (year, month, day) -> list of records
    buffers = defaultdict(list)

    latest_date = None
    processed = 0
    undated = 0

    print(f"Opening MBOX: {mbox_path}", file=sys.stderr)
    mbox = mailbox.mbox(mbox_path)

    try:
        for message in mbox:
            try:
                record = process_single_email(message, domain_party_map)
                if record is None:
                    continue

                # Route to the right day file
                year = record.get('year')
                month = record.get('month')
                day_num = record.get('day')

                if year and month and day_num:
                    buffers[(year, month, day_num)].append(record)
                    # Track latest date for watermark
                    if record.get('date'):
                        from datetime import datetime
                        try:
                            dt = datetime.fromisoformat(record['date'])
                            if latest_date is None or dt > latest_date:
                                latest_date = dt
                        except (ValueError, TypeError):
                            pass
                else:
                    # Undated emails go to a special file
                    undated += 1
                    buffers[('undated', 0, 0)].append(record)

                processed += 1
                if processed % 5000 == 0:
                    print(f"  ... processed {processed:,} emails", file=sys.stderr)

                # Flush large buffers to disk periodically
                for key in list(buffers.keys()):
                    if len(buffers[key]) >= 10000:
                        _flush_buffer(key, buffers[key])
                        buffers[key] = []

            except Exception as e:
                print(f"Error processing email: {e}", file=sys.stderr)
                continue
    finally:
        mbox.close()

    # Flush remaining buffers
    for key, records in buffers.items():
        if records:
            _flush_buffer(key, records)

    # Set watermark
    if latest_date:
        save_watermark(latest_date)
        print(f"  Watermark set to: {latest_date.isoformat()}", file=sys.stderr)

    print(f"\nMigration complete:", file=sys.stderr)
    print(f"  Processed: {processed:,} emails", file=sys.stderr)
    print(f"  Undated: {undated:,}", file=sys.stderr)


def _flush_buffer(key, records):
    """Append buffered records to the appropriate JSONL file.

    Loads any previously flushed records for this day and combines them,
    since the same day's buffer may be flushed multiple times during processing.
    """
    year, month, day_num = key

    if year == 'undated':
        path = DATA_DIR / "undated.jsonl"
    else:
        path = day_path(year, month, day_num)

    existing = load_jsonl(path)
    all_records = existing + records
    save_jsonl(path, all_records)


def main():
    parser = argparse.ArgumentParser(description="Migrate MBOX to daily JSONL files")
    parser.add_argument("--mbox-path", default=DEFAULT_MBOX_PATH,
                        help="Path to the MBOX file")
    parser.add_argument("--data-dir", default=None,
                        help="Override data directory (default: data/)")
    args = parser.parse_args()

    mbox_path = Path(args.mbox_path)
    if not mbox_path.exists():
        print(f"Error: MBOX file not found: {mbox_path}", file=sys.stderr)
        sys.exit(1)

    migrate(str(mbox_path), args.data_dir)


if __name__ == "__main__":
    main()
