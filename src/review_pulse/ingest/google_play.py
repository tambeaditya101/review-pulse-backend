"""Google Play Store review ingestion."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime

from review_pulse.models import Review

logger = logging.getLogger(__name__)

# Pagination config
_BATCH_SIZE = 200
_MAX_PAGES = 50  # Safety cap: 200 * 50 = 10,000 reviews max
_DELAY_SECONDS = 1.5  # Polite delay between pagination calls


def fetch_google_play_reviews(
    package_name: str,
    country: str,
    language: str,
    window_start: date,
    window_end: date,
) -> list[Review]:
    """Fetch Google Play reviews within the given date window.

    Paginates through reviews sorted by NEWEST until reviews fall outside
    the window. Applies a polite delay between pages.
    """
    # Lazy import to avoid import-time failures in test environments
    from google_play_scraper import Sort, reviews

    collected: list[Review] = []
    continuation_token = None

    for page in range(_MAX_PAGES):
        logger.debug(
            "Google Play page %d (token=%s)",
            page,
            "yes" if continuation_token else "no",
        )

        try:
            batch, continuation_token = reviews(
                package_name,
                lang=language,
                country=country,
                sort=Sort.NEWEST,
                count=_BATCH_SIZE,
                continuation_token=continuation_token,
            )
        except Exception:
            logger.exception(
                "Google Play scraper error on page %d", page
            )
            break

        if not batch:
            logger.debug("No more reviews returned; stopping pagination")
            break

        all_before_window = True
        for raw in batch:
            review = _normalize(raw)
            if review is None:
                continue

            # Skip reviews after the window end
            if review.review_date > window_end:
                all_before_window = False
                continue

            # Stop when reviews are before the window start
            if review.review_date < window_start:
                continue
            else:
                all_before_window = False

            collected.append(review)

        # If all reviews in this batch are before our window, stop
        if all_before_window and batch:
            logger.debug("All reviews in batch before window; stopping")
            break

        if not continuation_token:
            logger.debug("No continuation token; stopping pagination")
            break

        # Polite delay
        time.sleep(_DELAY_SECONDS)

    return collected


def _normalize(raw: dict) -> Review | None:
    """Convert a raw google-play-scraper dict to a Review."""
    try:
        text = raw.get("content") or ""
        if not text.strip():
            return None

        # Extract review date
        review_at = raw.get("at")
        if isinstance(review_at, datetime):
            review_date = review_at.date()
        elif isinstance(review_at, date):
            review_date = review_at
        else:
            return None

        rating = raw.get("score")
        if rating is None:
            return None

        # Generate deterministic ID
        review_id = _make_id(text, rating, review_date)

        return Review(
            review_id=review_id,
            source="google_play",
            text=text,
            rating=int(rating),
            review_date=review_date,
            title=None,  # Google Play reviews don't have separate titles
            author=raw.get("userName"),
            app_version=raw.get("reviewCreatedVersion"),
            fetched_at=datetime.now(),
        )
    except Exception:
        logger.debug("Failed to normalize Google Play review: %s", raw, exc_info=True)
        return None


def _make_id(text: str, rating: int, review_date: date) -> str:
    """SHA-256 based deterministic ID for dedup."""
    payload = f"{text.strip().lower()}|{rating}|{review_date.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
