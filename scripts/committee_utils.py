"""Shared helpers for committee backfill and enrichment.

Pure functions only (no ijson / Ollama / network imports) so they can be unit
tested without the 4 GB qwen file or a running Ollama instance.
"""

from utils import DATA_DIR

# Values the model emits that we treat as "no committee determined" -> None.
# Compared case-insensitively against the stripped value.
UNKNOWN_VALUES = {"", "unknown", "none", "n/a", "null"}

# A real committee name is short. Anything longer is a model that rambled
# (multi-paragraph "here is a summary..." essays) rather than answering.
MAX_COMMITTEE_LEN = 150

# Substrings that mark a natural-language non-answer ("No committee name found
# in the email text", "I cannot determine...") rather than a committee. These
# never appear in a real committee name, so a substring match is safe.
ABSTENTION_MARKERS = (
    "no committee", "not found", "cannot determine", "cannot be determined",
    "unable to", "not able to", "could not", "couldn't", "not present",
    "not specified", "not mentioned", "not explicitly", "no political",
    "no clear", "i cannot", "there is no", "not identified",
)


def normalize_committee(value):
    """Return a clean committee name, or None for unknown/empty/garbage values.

    Real names are written verbatim (stripped only). Values are rejected to None
    when they are unknown/empty, absurdly long (a rambling model response),
    contain DSPy ChatAdapter field markers ("[[", "##") that leak from
    misbehaving models, or read as a natural-language "I can't tell" non-answer.
    """
    if value is None:
        return None
    stripped = str(value).strip()
    if stripped.lower() in UNKNOWN_VALUES:
        return None
    if len(stripped) > MAX_COMMITTEE_LEN:
        return None
    if "[[" in stripped or "##" in stripped:
        return None
    low = stripped.lower()
    if any(marker in low for marker in ABSTENTION_MARKERS):
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
