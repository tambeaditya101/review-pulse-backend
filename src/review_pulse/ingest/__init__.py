"""Review ingestion — fetch and normalize reviews from all sources."""

from __future__ import annotations

import logging
from datetime import date

from review_pulse.config import AppConfig
from review_pulse.models import Review

logger = logging.getLogger(__name__)


def fetch_all_reviews(
    config: AppConfig,
    window_start: date,
    window_end: date,
) -> list[Review]:
    """Fetch reviews from all configured sources within the date window.

    Args:
        config: Product configuration with store identifiers.
        window_start: Earliest review date to include (inclusive).
        window_end: Latest review date to include (inclusive).

    Returns:
        Combined list of normalized ``Review`` objects from all sources.
    """
    from review_pulse.ingest.app_store import fetch_app_store_reviews
    from review_pulse.ingest.google_play import fetch_google_play_reviews

    all_reviews: list[Review] = []

    # --- Google Play ---
    try:
        gp_reviews = fetch_google_play_reviews(
            package_name=config.product.google_play_package,
            country=config.product.country,
            language=config.product.language,
            window_start=window_start,
            window_end=window_end,
        )
        logger.info("Google Play: fetched %d reviews", len(gp_reviews))
        all_reviews.extend(gp_reviews)
    except Exception:
        logger.exception("Failed to fetch Google Play reviews")

    # --- App Store ---
    try:
        as_reviews = fetch_app_store_reviews(
            app_id=config.product.app_store_id,
            country=config.product.country,
            window_start=window_start,
            window_end=window_end,
        )
        logger.info("App Store: fetched %d reviews", len(as_reviews))
        all_reviews.extend(as_reviews)
    except Exception:
        logger.exception("Failed to fetch App Store reviews")

    logger.info(
        "Total reviews fetched: %d (GP=%d, AS=%d)",
        len(all_reviews),
        sum(1 for r in all_reviews if r.source == "google_play"),
        sum(1 for r in all_reviews if r.source == "app_store"),
    )

    return all_reviews
