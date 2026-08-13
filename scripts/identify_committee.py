"""
IdentifyCommitteeModule
-----------------------
Extracts the political committee that sponsored a fundraising email.

Strategy:
  1. Deterministic parsing: look for a "Paid for by <NAME>" disclaimer and clean
     the captured text (scripts/committee_extract.py).
  2. If the parsed result doesn't look like a plausible committee name, fall
     back to an LLM call (dspy.Predict).

The returned dspy.Prediction carries `.committee` and `.source`
("disclaimer" when the deterministic parse answered, "llm" when the fallback
did) so callers can record provenance.
"""

from pathlib import Path

import dspy

# Re-export the pure extraction helpers so existing importers of this module
# (and external projects) keep working after the split into committee_extract.
from committee_extract import (  # noqa: F401
    DANGLING_WORDS,
    LEGAL_SUFFIX_RE,
    PAID_FOR_BY_RE,
    STOP_PATTERNS,
    clean,
    extract_committee,
    extract_from_tail,
    fix_spacing,
    looks_confident,
    strip_parenthetical,
    strip_trailing_conjunctions,
    trim_after_comma,
    truncate_at_legal_suffix,
)

# When a disclaimer is present the committee name comes ONLY from the disclaimer
# text, never from the sender's name/signature. The deterministic pass enforces
# this structurally; the fallback is instructed to do the same.
FALLBACK_INSTRUCTIONS = (
    "Identify the political committee that sponsored a fundraising email. "
    "The committee's name is present in the email text itself. "
    "If the email contains a disclaimer (for example 'Paid for by ...'), the "
    "committee name MUST be taken from the disclaimer text ONLY -- never from "
    "the sender's name, signature, or 'from' line. Only when no disclaimer "
    "exists may the sender's identity inform the answer."
)


# A GEPA-optimized fallback prompt, if scripts/optimize_fallback.py produced one.
OPTIMIZED_PATH = Path(__file__).resolve().parent.parent / "config" / "fallback_optimized.json"


class IdentifyCommitteeModule(dspy.Module):
    def __init__(self):
        super().__init__()
        # LLM fallback, used only when deterministic parsing fails.
        self.fallback = dspy.Predict(
            dspy.Signature("email_body: str -> committee: str", FALLBACK_INSTRUCTIONS)
        )
        # Prefer a GEPA-optimized prompt when one has been saved.
        if OPTIMIZED_PATH.exists():
            try:
                self.fallback.load(str(OPTIMIZED_PATH))
            except Exception:  # noqa: BLE001 - fall back to the bare signature
                pass

    def forward(self, **inputs):
        email_body = inputs.get("email_body", "") or ""

        # 1. Deterministic pass over the "Paid for by ..." disclaimer.
        name = extract_committee(email_body)
        if looks_confident(name):
            return dspy.Prediction(committee=name, source="disclaimer")

        # 2. LLM fallback when the disclaimer parse is missing/implausible.
        r = self.fallback(email_body=email_body)
        result = clean(r.committee) or (r.committee if r.committee else "")
        return dspy.Prediction(committee=result, source="llm")
