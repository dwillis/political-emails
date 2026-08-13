"""
IdentifyCommitteeModule
-----------------------
Extracts the political committee that sponsored a fundraising email.

Strategy:
  1. Deterministic parsing: look for a "Paid for by <NAME>" disclaimer,
     then aggressively clean the captured text (strip addresses, legal
     boilerplate, URLs, dangling words, duplicated phrases, etc.).
  2. If the parsed result doesn't look like a plausible committee name,
     fall back to an LLM call (dspy.Predict).
"""

import re

import dspy


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
    r"\(\s*\)",           # empty parentheses
    r"\d{2,}\s+[A-Za-z]", # a street number followed by a word (an address)
    r"\.\s+[A-Z]",        # sentence boundary
    r"\.\s*$",            # trailing period at end of tail
    r"\s\|\s",            # pipe separator
    r"\s#\S",             # hashtag / unit number
]

# Words that commonly end a committee's legal name. If one appears, the
# name is truncated right after its *last* occurrence.
LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:PAC|Committee|Congress|Senate|Inc|Incorporated|LLC|"
    r"Fund|Party|Account|Victory|Campaign|State|House|President|"
    r"Values|Action)\b",
    flags=re.IGNORECASE,
)

# "Paid for by <NAME>" (also "Paid for and authorized by <NAME>").
# Whitespace after "by" is optional so glued text like
# "Paid for byJames for NY 2026" is still captured; spacing is fixed later.
PAID_FOR_BY_RE = re.compile(
    r"[Pp][Aa][Ii][Dd]\s*for\s*(?:and\s*authorized\s*)?by\s*(?:the\s+)?(.+)",
    flags=re.IGNORECASE,
)

# Words that shouldn't be left dangling at the end of a name.
DANGLING_WORDS = {"and", "or", "the", "a", "an", "of", "for", "to", "by", "with"}


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

def clean(name):
    """Normalize whitespace and strip surrounding punctuation."""
    if name is None:
        return None
    name = name.replace("\n", " ").replace("\r", " ")
    name = re.sub(r"\s+", " ", name).strip()
    name = name.strip(" .,:;-—–()[]{}\"'|")
    return name.strip()


def fix_spacing(name):
    """Insert a space at lowercase→uppercase boundaries ("PaidForBy" glue)."""
    if not name:
        return name
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def trim_after_comma(name):
    """Keep only the text before the first comma."""
    idx = name.find(",")
    return name[:idx] if idx != -1 else name


def strip_parenthetical(name):
    """Drop everything from the first opening parenthesis onward."""
    idx = name.find("(")
    return name[:idx].strip() if idx != -1 else name


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
    name = fix_spacing(name)
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
    if "(" in name or ")" in name:
        return False
    if not re.search(r"[A-Za-z]", name):
        return False
    return True


# ---------------------------------------------------------------------------
# DSPy module
# ---------------------------------------------------------------------------

class IdentifyCommitteeModule(dspy.Module):
    def __init__(self):
        super().__init__()
        # LLM fallback, used only when deterministic parsing fails.
        self.fallback = dspy.Predict(
            dspy.Signature(
                "email_body: str -> committee: str",
                "Identify the political committee that sponsored a fundraising "
                "email. The committee's name is present in the email text itself.",
            )
        )

    def forward(self, **inputs):
        email_body = inputs.get("email_body", "") or ""

        # 1. Deterministic pass: find every "Paid for by ..." occurrence.
        candidates = []
        for m in PAID_FOR_BY_RE.finditer(email_body):
            name = extract_from_tail(m.group(1))
            if name and len(name) > 2:
                candidates.append(name)

        result = candidates[0] if candidates else None

        # 2. LLM fallback if the parsed name doesn't look plausible.
        if not looks_confident(result):
            r = self.fallback(email_body=email_body)
            result = clean(r.committee) or (r.committee if r.committee else "")

        return dspy.Prediction(committee=result)
