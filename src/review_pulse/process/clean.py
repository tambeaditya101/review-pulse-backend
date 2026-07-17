"""Clean and deduplicate review text."""

from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import date

from review_pulse.models import Review

logger = logging.getLogger(__name__)

# --- Regex patterns for cleaning ---

# Zero-width characters (ZWJ, ZWNJ, ZWSP, BOM, soft-hyphen, etc.)
_ZERO_WIDTH_RE = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064"
    r"\ufeff\u00ad\u034f\u180e]"
)

# Collapse multiple whitespace characters into a single space
_WHITESPACE_RE = re.compile(r"\s+")

# Minimum review length after cleaning
_MIN_REVIEW_LENGTH = 10


def clean_text(text: str) -> str:
    """Clean a single review text string.

    Steps:
        1. Unescape HTML entities (``&amp;`` → ``&``, etc.)
        2. Remove zero-width / invisible Unicode characters
        3. Collapse runs of whitespace to a single space
        4. Strip leading/trailing whitespace
    """
    # 1. HTML entities
    cleaned = html.unescape(text)

    # 2. Zero-width chars
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)

    # 3. Collapse whitespace
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)

    # 4. Strip
    cleaned = cleaned.strip()

    return cleaned


def dedup_key(text: str, rating: int, review_date: date) -> str:
    """Compute a SHA-256 dedup fingerprint from normalized text + rating + date."""
    normalized = text.strip().lower()
    payload = f"{normalized}|{rating}|{review_date.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clean_and_deduplicate(
    reviews: list[Review],
    window_start: date,
    window_end: date,
) -> list[Review]:
    """Clean, deduplicate, and filter a list of reviews.

    Processing steps:
        1. Clean text (HTML unescape, zero-width removal, whitespace collapse)
        2. Drop reviews shorter than ``_MIN_REVIEW_LENGTH`` chars after cleaning
        3. Drop reviews outside the ``[window_start, window_end]`` date range
        4. Deduplicate on ``sha256(normalized_text + rating + date)``

    Args:
        reviews: Raw review list (will not be mutated).
        window_start: Earliest date to keep (inclusive).
        window_end: Latest date to keep (inclusive).

    Returns:
        New list of cleaned, deduplicated ``Review`` objects.
    """
    seen_keys: set[str] = set()
    cleaned: list[Review] = []
    stats = {"short": 0, "out_of_window": 0, "duplicate": 0, "kept": 0}

    for review in reviews:
        # --- Clean text ---
        text = clean_text(review.text)

        # --- Filter: too short ---
        if len(text) < _MIN_REVIEW_LENGTH:
            stats["short"] += 1
            continue

        # --- Filter: outside window ---
        if review.review_date < window_start or review.review_date > window_end:
            stats["out_of_window"] += 1
            continue

        # --- Filter: duplicate ---
        key = dedup_key(text, review.rating, review.review_date)
        if key in seen_keys:
            stats["duplicate"] += 1
            continue
        seen_keys.add(key)

        # --- Create cleaned copy ---
        cleaned_review = Review(
            review_id=review.review_id,
            source=review.source,
            text=text,
            rating=review.rating,
            review_date=review.review_date,
            title=review.title,
            author=review.author,
            app_version=review.app_version,
            fetched_at=review.fetched_at,
        )
        cleaned.append(cleaned_review)
        stats["kept"] += 1

    if cleaned:
        c_dates = [r.review_date for r in cleaned]
        logger.info(
            "[CLEANING_DONE] Cleaned stats: kept=%d, short=%d, out_of_window=%d, duplicate=%d (from %d total). Earliest retained: %s, Latest retained: %s",
            stats["kept"],
            stats["short"],
            stats["out_of_window"],
            stats["duplicate"],
            len(reviews),
            min(c_dates).isoformat(),
            max(c_dates).isoformat()
        )
    else:
        logger.info(
            "[CLEANING_DONE] Cleaned stats: kept=0, short=%d, out_of_window=%d, duplicate=%d (from %d total)",
            stats["short"],
            stats["out_of_window"],
            stats["duplicate"],
            len(reviews)
        )

    return cleaned
