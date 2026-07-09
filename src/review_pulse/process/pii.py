"""PII scrubbing — redact personal information from review text.

Patterns targeted:
    - Email addresses
    - Indian phone numbers (10-digit with optional +91 / 0 prefix)
    - PAN card numbers (ABCDE1234F)
    - Aadhaar-like numbers (12 digits, optionally space/dash separated)
    - Long digit sequences (≥ 8 consecutive digits not caught above)
"""

from __future__ import annotations

import re

_REDACTED = "[REDACTED]"

# ──────────────────────────────────────────────────────────────
#  Compiled regex patterns (order matters — more specific first)
# ──────────────────────────────────────────────────────────────

# Email: standard RFC-ish pattern
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# Indian PAN: 5 uppercase letters, 4 digits, 1 uppercase letter
_PAN_RE = re.compile(
    r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"
)

# Aadhaar-like: 12 digits, optionally separated by spaces or dashes
# e.g., 1234 5678 9012  or  1234-5678-9012  or  123456789012
_AADHAAR_RE = re.compile(
    r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"
)

# Indian phone: optional +91 or 0 prefix, then 10 digits
# Handles: +91-9876543210, +919876543210, 09876543210, 9876543210
_PHONE_IN_RE = re.compile(
    r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{9}(?!\d)"
)

# Long digit sequences: 8+ consecutive digits (catch-all for account numbers, etc.)
_LONG_DIGITS_RE = re.compile(
    r"\b\d{8,}\b"
)


# Order: specific patterns before the generic long-digit catch-all
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", _EMAIL_RE),
    ("pan", _PAN_RE),
    ("aadhaar", _AADHAAR_RE),
    ("phone_in", _PHONE_IN_RE),
    ("long_digits", _LONG_DIGITS_RE),
]


def scrub_pii(text: str) -> str:
    """Replace all recognised PII patterns in *text* with ``[REDACTED]``.

    Patterns are applied in order of specificity (email, PAN, Aadhaar,
    phone, then generic long-digit sequences).
    """
    result = text
    for _name, pattern in _PATTERNS:
        result = pattern.sub(_REDACTED, result)
    return result


def scrub_reviews(reviews: list[dict | object]) -> list:
    """Scrub PII from a list of Review-like objects (mutates `.text` in place).

    Accepts either dicts with a ``text`` key or objects with a ``text`` attribute.
    Returns the same list for chaining.
    """
    for review in reviews:
        if isinstance(review, dict):
            review["text"] = scrub_pii(review["text"])
        else:
            review.text = scrub_pii(review.text)  # type: ignore[attr-defined]
    return reviews
