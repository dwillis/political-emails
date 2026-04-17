"""Shared helpers for JSONL I/O, deduplication, and path management."""

import json
import os
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def day_path(year, month, day):
    """Return the JSONL path for a specific day: data/YYYY/MM/YYYY-MM-DD.jsonl."""
    dir_path = DATA_DIR / str(year) / f"{month:02d}"
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / f"{year}-{month:02d}-{day:02d}.jsonl"


def current_day_path():
    """Return the JSONL path for today."""
    today = date.today()
    return day_path(today.year, today.month, today.day)


def load_jsonl(path):
    """Load a JSONL file into a list of dicts. Returns [] if file doesn't exist."""
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def save_jsonl(path, records):
    """Write records to a JSONL file, sorted by (date, message_id) for stable diffs."""
    def sort_key(r):
        d = r.get("date") or ""
        m = r.get("message_id") or ""
        return (d, m)

    sorted_records = sorted(records, key=sort_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for record in sorted_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_day_message_ids(path):
    """Load all message_ids from a daily JSONL file into a set for dedup."""
    ids = set()
    if not os.path.exists(path):
        return ids
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                record = json.loads(line)
                mid = record.get("message_id")
                if mid:
                    ids.add(mid)
    return ids


def load_watermark():
    """Load the watermark (last processed date) from state/watermark.json.

    Returns a datetime or None if no watermark exists.
    """
    wm_path = STATE_DIR / "watermark.json"
    if not wm_path.exists():
        return None
    with open(wm_path) as f:
        data = json.load(f)
    date_str = data.get("last_processed_date")
    if not date_str:
        return None
    return datetime.fromisoformat(date_str)


def save_watermark(dt):
    """Save the watermark to state/watermark.json."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    wm_path = STATE_DIR / "watermark.json"
    data = {
        "last_processed_date": dt.isoformat(),
        "last_run": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(wm_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def count_records(path):
    """Count non-empty lines in a JSONL file."""
    count = 0
    with open(path) as f:
        for line in f:
            if line.strip():
                count += 1
    return count
