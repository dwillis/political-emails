"""Shared helpers for committee backfill and enrichment.

Pure functions only (no ijson / Ollama / network imports) so they can be unit
tested without the 4 GB qwen file or a running Ollama instance.
"""

from utils import DATA_DIR

# Values the model emits that we treat as "no committee determined" -> None.
# Compared case-insensitively against the stripped value.
UNKNOWN_VALUES = {"", "unknown", "none", "n/a", "null"}


def normalize_committee(value):
    """Return a clean committee name, or None for unknown/empty values.

    Names are written verbatim (stripped only); no other normalization.
    """
    if value is None:
        return None
    stripped = str(value).strip()
    if stripped.lower() in UNKNOWN_VALUES:
        return None
    return stripped


def normalize_date(date_str):
    """Bridge the qwen date format to the archive's ISO format.

    qwen: "2026-01-18 18:14:58+00:00"  ->  archive: "2026-01-18T18:14:58+00:00"
    Only the first space (date/time separator) is swapped for a 'T'.
    """
    return (date_str or "").strip().replace(" ", "T", 1)


def join_key(email, subject, date_str):
    """Build a case-insensitive join key from the fields shared by both datasets.

    Tolerates None for any field. Archive records with date=None simply never
    match a qwen record (their key's date component is "").
    """
    return (
        (email or "").lower(),
        (subject or "").lower(),
        normalize_date(date_str),
    )


def record_key(record):
    """Build a join_key from a record dict (same field names in both datasets)."""
    return join_key(record.get("email"), record.get("subject"), record.get("date"))


def build_committee_map(records_iter):
    """Build a {join_key: committee_or_None} map from an iterable of qwen records.

    Accepts any iterable (a list in tests, the ijson stream in the backfill).

    Collision handling (only ~102 keys collide across the archive):
      - agreeing values (or a repeat)      -> keep the value
      - None vs a real value               -> keep the real value
      - two different real values          -> map to None, count as a conflict

    Returns (mapping, stats) where stats has: parsed, mapped_real, mapped_null,
    conflicts, distinct_keys.
    """
    mapping = {}
    stats = {"parsed": 0, "conflicts": 0}

    for record in records_iter:
        stats["parsed"] += 1
        key = record_key(record)
        value = normalize_committee(record.get("committee"))

        if key not in mapping:
            mapping[key] = value
            continue

        existing = mapping[key]
        if existing == value:
            continue
        if existing is None:
            mapping[key] = value
        elif value is None:
            pass  # keep the real existing value
        else:
            # two different real values -> ambiguous, blank it out
            mapping[key] = None
            stats["conflicts"] += 1

    stats["distinct_keys"] = len(mapping)
    stats["mapped_real"] = sum(1 for v in mapping.values() if v is not None)
    stats["mapped_null"] = stats["distinct_keys"] - stats["mapped_real"]
    return mapping, stats


def iter_day_files():
    """Yield all archive JSONL paths, sorted.

    Covers the dated files under data/YYYY/MM/ plus any top-level files such as
    data/undated.jsonl (dateless records from the mbox migration), so every
    archive record gets a committee key.
    """
    dated = DATA_DIR.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.jsonl")
    top_level = DATA_DIR.glob("*.jsonl")
    return sorted([*dated, *top_level])


def needs_committee(record):
    """True if the record has no committee assigned yet (missing or null)."""
    return record.get("committee") is None
