"""Apple App Store review ingestion."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime, timezone

from review_pulse.models import Review

logger = logging.getLogger(__name__)

# Fetch config
_HOW_MANY = 500  # Reviews to request per fetch (library handles internal paging)
_DELAY_SECONDS = 2.0  # Delay before retrying on failure
_MAX_RETRIES = 3


def fetch_app_store_reviews(
    app_id: int,
    country: str,
    window_start: date,
    window_end: date,
) -> list[Review]:
    """Fetch App Store reviews within the given date window.

    Uses the ``app_store_scraper`` library to pull recent reviews,
    then filters by date.
    """
    # Lazy import to avoid import-time failures in test environments
    from app_store_scraper import AppStore

    raw_reviews: list[dict] = []

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            app = AppStore(
                country=country,
                app_name="indmoney",
                app_id=app_id,
            )
            app.review(how_many=_HOW_MANY)
            raw_reviews = app.reviews or []
            logger.info(
                "App Store: retrieved %d raw reviews (attempt %d)",
                len(raw_reviews),
                attempt,
            )
            break
        except Exception:
            logger.warning(
                "App Store fetch attempt %d/%d failed",
                attempt,
                _MAX_RETRIES,
                exc_info=True,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_DELAY_SECONDS * attempt)
    else:
        logger.error("App Store fetch failed after %d retries", _MAX_RETRIES)
        return []

    logger.info(
        "[APP_STORE_FETCH] Starting fetch: app_id=%d window_start=%s window_end=%s",
        app_id, window_start.isoformat(), window_end.isoformat()
    )

    # Filter + normalize
    collected: list[Review] = []
    for raw in raw_reviews:
        review = _normalize(raw)
        if review is None:
            continue
        if review.review_date < window_start or review.review_date > window_end:
            continue
        collected.append(review)

    if collected:
        c_dates = [r.review_date for r in collected]
        logger.info(
            "[APP_STORE_FETCH_DONE] Fetched %d reviews within window. earliest=%s latest=%s",
            len(collected), min(c_dates).isoformat(), max(c_dates).isoformat()
        )
    else:
        logger.info("[APP_STORE_FETCH_DONE] Fetched 0 reviews within window.")

    return collected


def _normalize(raw: dict) -> Review | None:
    """Convert a raw app_store_scraper review dict to a Review."""
    try:
        text = raw.get("review") or ""
        if not text.strip():
            return None

        # Extract review date
        review_at = raw.get("date")
        if isinstance(review_at, datetime):
            review_date = review_at.date()
        elif isinstance(review_at, date):
            review_date = review_at
        else:
            return None

        rating = raw.get("rating")
        if rating is None:
            return None

        review_id = _make_id(text, int(rating), review_date)

        return Review(
            review_id=review_id,
            source="app_store",
            text=text,
            rating=int(rating),
            review_date=review_date,
            title=raw.get("title"),
            author=raw.get("userName"),
            app_version=raw.get("isCurrentVersion"),
            fetched_at=datetime.now(timezone.utc),
        )
    except Exception:
        logger.debug("Failed to normalize App Store review: %s", raw, exc_info=True)
        return None


def _make_id(text: str, rating: int, review_date: date) -> str:
    """SHA-256 based deterministic ID for dedup."""
    payload = f"{text.strip().lower()}|{rating}|{review_date.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
