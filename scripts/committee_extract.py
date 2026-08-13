"""Deterministic committee-name extraction from a "Paid for by ..." disclaimer.

Pure regex/string code with no third-party imports (no dspy), so it can be used
by the validation/backfill scripts and unit tests without the enrich deps. The
DSPy module in identify_committee.py wraps this and adds an LLM fallback.
"""

import re


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Text that signals the committee name has ended (addresses, disclaimers,
# unsubscribe links, and other boilerplate that follows the name).
STOP_PATTERNS = [
    r"\bP\.?\s?O\.?\s*Box\b",
    r"\bPO\s+Box\b",
    r"\bTreasurer\b",
    r"\bThis email\b",
    r"\bThis message\b",
    r"\bThis is a\b",
    r"\bYou are receiving\b",
    r"\bYou can\b",
    r"\bDon'?t\b",
    r"\bWe Need\b",
    r"\bNOT\s*AUTHORIZED\b",
    r"\bNot\s*authorized\b",
    r"\bNotauthorized\b",
    r"\band not authorized\b",
    r"\bAND\s+NOT\s+AUTHORIZED\b",
    r"\bAuthorized by\b",
    r"\ba joint fundraising\b",
    r"\bUse of\b",
    r"\bIf you\b",
    r"\bPlease support\b",
    r"\bPlease\b",
    r"\bYour Support\b",
    r"\bcopyright\b",
    r"\bwww\.",
    r"\bhttp",
    r"\bclick here\b",
    r"\bUnsubscribe\b",
    r"\bAll rights reserved\b",
    r"\bReceive less\b",
    r"\bReceive fewer\b",
    r"\bDrawer\b",
    r"\bStreet\b",
    r"\bSuite\b",
    r"\bContributions\b",
    r"\bEmail is\b",       # boilerplate: "... Email is a critical way to stay"
    r"\bEmails? are\b",
    r"\(\s*\)",           # empty parentheses
    r"\d{2,}\s+[A-Za-z]", # a street number followed by a word (an address)
    # sentence boundary, but not after an abbreviation (Dr. / U.S. / Mr. / St.)
    r"(?<![A-Z])(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bSt)(?<!\bJr)(?<!\bSr)\.\s+[A-Z]",
    r"\.\s*$",            # trailing period at end of tail
    r"\s\|\s",            # pipe separator
    r"\s#\S",             # hashtag / unit number
]

# Words that reliably TERMINATE a committee's legal name; the name is truncated
# right after the last occurrence to drop trailing address/boilerplate. Only
# corporate/committee designators belong here -- common words like "Action",
# "Party", "State", "House" appear mid-name ("Heritage Action for America",
# "Democratic Party of Georgia") and must NOT trigger truncation.
LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:PAC|Committee|Incorporated|Inc|LLC|Fund)\b",
    flags=re.IGNORECASE,
)

# "Paid for by <NAME>" (also "Paid for and authorized by <NAME>").
# Whitespace after "by" is optional so glued text like
# "Paid for byJames for NY 2026" is still captured.
PAID_FOR_BY_RE = re.compile(
    r"[Pp][Aa][Ii][Dd]\s*for\s*(?:and\s*authorized\s*)?by\s*(?:the\s+)?(.+)",
    flags=re.IGNORECASE,
)

# A comma that introduces a legal-entity suffix (", Inc.", ", LLC") is part of
# the name and must not be treated as a truncation point.
COMMA_ENTITY_RE = re.compile(
    r",\s*(?:Inc\b\.?|Incorporated\b|LLC\b|L\.L\.C\.?|Ltd\b\.?|Co\b\.?)",
    flags=re.IGNORECASE,
)

# Words that shouldn't be left dangling at the end of a name.
DANGLING_WORDS = {"and", "or", "the", "a", "an", "of", "for", "to", "by", "with"}

# A short, closed, alphabetic parenthetical is a meaningful disambiguator
# ("(Federal)", "(TMFP)") and is kept; anything else is stripped.
KEEP_PAREN_RE = re.compile(r"\(([A-Za-z][A-Za-z &.\-]{0,30})\)")


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

def clean(name):
    """Normalize whitespace and strip surrounding punctuation."""
    if name is None:
        return None
    name = name.replace("\n", " ").replace("\r", " ")
    name = re.sub(r"\s+", " ", name).strip()
    name = name.strip(" .,:;-—–[]{}\"'|")
    return name.strip()


def fix_spacing(name):
    """Deprecated: split lowercase->uppercase boundaries ("PaidForBy" glue).

    No longer used in the cleanup chain — it corrupted CamelCase surnames
    (DeSantis -> "De Santis"), and PAID_FOR_BY_RE already de-glues start text.
    Kept only for backward compatibility with external importers.
    """
    if not name:
        return name
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def trim_after_comma(name):
    """Keep only the text before the first comma, unless the comma introduces a
    legal-entity suffix ("Never Surrender, Inc." keeps its suffix)."""
    idx = -1
    while (idx := name.find(",", idx + 1)) != -1:
        if not COMMA_ENTITY_RE.match(name, idx):
            return name[:idx]
    return name


def strip_parenthetical(name):
    """Drop parentheticals from the first "(" onward, but keep a short closed
    alphabetic one ("(Federal)")."""
    idx = name.find("(")
    if idx == -1:
        return name
    if KEEP_PAREN_RE.match(name, idx) and name.count("(") == 1 and name.count(")") == 1:
        return name
    return name[:idx].strip()


def truncate_at_legal_suffix(name):
    """Cut the name right after the last legal-suffix word (PAC, Fund, ...)."""
    last_match = None
    for m in LEGAL_SUFFIX_RE.finditer(name):
        last_match = m
    return name[: last_match.end()] if last_match else name


def strip_trailing_conjunctions(name):
    """Remove dangling words like 'and', 'for', 'the' from the end."""
    words = name.split()
    while words and words[-1].lower() in DANGLING_WORDS:
        words.pop()
    return " ".join(words)


def extract_from_tail(tail):
    """Given the text after 'Paid for by', extract a clean committee name."""
    # Cut at the earliest stop pattern.
    end = len(tail)
    for pattern in STOP_PATTERNS:
        m = re.search(pattern, tail, flags=re.IGNORECASE)
        if m and m.start() < end:
            end = m.start()
    raw = tail[:end]

    # Progressive cleanup.
    raw = trim_after_comma(raw)
    raw = strip_parenthetical(raw)
    name = clean(raw)
    if not name:
        return None

    name = truncate_at_legal_suffix(name)
    name = strip_parenthetical(name)
    name = clean(name)
    name = strip_trailing_conjunctions(name)
    name = clean(name)
    if not name:
        return None

    # Collapse duplicated phrases, e.g. "Smith PAC Smith PAC" -> "Smith PAC".
    words = name.split()
    n = len(words)
    for k in range(1, n // 2 + 1):
        first = " ".join(words[:k]).lower()
        second = " ".join(words[k:2 * k]).lower()
        if first == second:
            name = " ".join(words[:k])
            break

    # Drop a leading "The" and any newly dangling words.
    if name.lower().startswith("the "):
        name = name[4:].strip()
    name = strip_trailing_conjunctions(name)
    return name


def looks_confident(name):
    """Heuristic sanity check: does this look like a real committee name?"""
    if not name:
        return False

    word_count = len(name.split())
    if word_count < 1 or word_count > 12:
        return False

    low = name.lower()
    bad_substrings = [
        "unsubscribe", "click here", "http", "www.",
        "receiving", "rights reserved",
        "we need", "please support", "your support",
        "not authorized", "notauthorized", "authorized by",
    ]
    if any(b in low for b in bad_substrings):
        return False

    if name.split()[-1].lower() in DANGLING_WORDS:
        return False
    # Reject only unbalanced parentheses; a balanced "(Federal)" is fine.
    if name.count("(") != name.count(")"):
        return False
    if not re.search(r"[A-Za-z]", name):
        return False
    return True


def extract_committee(email_body):
    """Return the first plausible "Paid for by" committee name, or None.

    Mirrors the deterministic pass of IdentifyCommitteeModule.forward: the first
    'Paid for by' match whose cleaned tail is longer than 2 chars. Callers decide
    acceptance via looks_confident().
    """
    for m in PAID_FOR_BY_RE.finditer(email_body or ""):
        name = extract_from_tail(m.group(1))
        if name and len(name) > 2:
            return name
    return None
