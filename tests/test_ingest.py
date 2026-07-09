"""Tests for review_pulse.ingest — Google Play + App Store normalization.

Uses fixture JSON files to test normalization without live scraping.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from review_pulse.config import AppConfig, load_config
from review_pulse.ingest import fetch_all_reviews
from review_pulse.ingest.app_store import _normalize as as_normalize
from review_pulse.ingest.google_play import _normalize as gp_normalize
from review_pulse.models import Review

FIXTURES_DIR = Path(__file__).parent / "fixtures"
WINDOW_START = date(2026, 6, 1)
WINDOW_END = date(2026, 6, 30)


# ──────────────────────────────────────────────────────────────
#  Fixture loading helpers
# ──────────────────────────────────────────────────────────────


def load_gp_fixture() -> list[dict]:
    raw = json.loads((FIXTURES_DIR / "google_play_sample.json").read_text())
    # Simulate the datetime objects the scraper returns
    for item in raw:
        if "at" in item and item["at"]:
            item["at"] = datetime.fromisoformat(item["at"].replace("Z", "+00:00"))
    return raw


def load_as_fixture() -> list[dict]:
    raw = json.loads((FIXTURES_DIR / "app_store_sample.json").read_text())
    for item in raw:
        if "date" in item and item["date"]:
            item["date"] = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
    return raw


# ──────────────────────────────────────────────────────────────
#  Google Play normalization
# ──────────────────────────────────────────────────────────────


class TestGooglePlayNormalize:
    def test_valid_review(self) -> None:
        raw = load_gp_fixture()
        review = gp_normalize(raw[0])
        assert review is not None
        assert review.source == "google_play"
        assert review.rating == 5
        assert "mutual fund" in review.text.lower()

    def test_empty_content_returns_none(self) -> None:
        raw = load_gp_fixture()
        # Fixture index 8 has empty content
        review = gp_normalize(raw[8])
        assert review is None

    def test_missing_score_returns_none(self) -> None:
        raw = load_gp_fixture()
        # Fixture index 9 has no score
        review = gp_normalize(raw[9])
        assert review is None

    def test_review_id_is_deterministic(self) -> None:
        raw = load_gp_fixture()
        r1 = gp_normalize(raw[0])
        r2 = gp_normalize(raw[0])
        assert r1 is not None and r2 is not None
        assert r1.review_id == r2.review_id

    def test_all_valid_reviews_normalized(self) -> None:
        raw = load_gp_fixture()
        normalized = [gp_normalize(r) for r in raw]
        valid = [r for r in normalized if r is not None]
        # Fixture has 10 entries; 2 should be None (empty content + missing score)
        assert len(valid) == 8


# ──────────────────────────────────────────────────────────────
#  App Store normalization
# ──────────────────────────────────────────────────────────────


class TestAppStoreNormalize:
    def test_valid_review(self) -> None:
        raw = load_as_fixture()
        review = as_normalize(raw[0])
        assert review is not None
        assert review.source == "app_store"
        assert review.rating == 5
        assert review.title == "Best investment app"

    def test_empty_review_text_returns_none(self) -> None:
        raw = load_as_fixture()
        # Fixture index 6 has empty review text
        review = as_normalize(raw[6])
        assert review is None

    def test_review_id_is_deterministic(self) -> None:
        raw = load_as_fixture()
        r1 = as_normalize(raw[0])
        r2 = as_normalize(raw[0])
        assert r1 is not None and r2 is not None
        assert r1.review_id == r2.review_id

    def test_all_valid_reviews_normalized(self) -> None:
        raw = load_as_fixture()
        normalized = [as_normalize(r) for r in raw]
        valid = [r for r in normalized if r is not None]
        # 7 entries; 1 has empty text
        assert len(valid) == 6


# ──────────────────────────────────────────────────────────────
#  fetch_all_reviews (mocked scrapers)
# ──────────────────────────────────────────────────────────────


class TestFetchAllReviews:
    @patch("review_pulse.ingest.app_store.fetch_app_store_reviews")
    @patch("review_pulse.ingest.google_play.fetch_google_play_reviews")
    def test_combines_sources(
        self, mock_gp: MagicMock, mock_as: MagicMock
    ) -> None:
        gp_review = Review(
            review_id="gp1",
            source="google_play",
            text="Google review",
            rating=4,
            review_date=date(2026, 6, 15),
        )
        as_review = Review(
            review_id="as1",
            source="app_store",
            text="App Store review",
            rating=5,
            review_date=date(2026, 6, 16),
        )

        mock_gp.return_value = [gp_review]
        mock_as.return_value = [as_review]

        config = load_config("indmoney")
        results = fetch_all_reviews(config, WINDOW_START, WINDOW_END)

        assert len(results) == 2
        sources = {r.source for r in results}
        assert sources == {"google_play", "app_store"}

    @patch("review_pulse.ingest.app_store.fetch_app_store_reviews")
    @patch("review_pulse.ingest.google_play.fetch_google_play_reviews")
    def test_handles_gp_failure_gracefully(
        self, mock_gp: MagicMock, mock_as: MagicMock
    ) -> None:
        mock_gp.side_effect = Exception("Scraper broken")
        mock_as.return_value = [
            Review(
                review_id="as1",
                source="app_store",
                text="Still works",
                rating=4,
                review_date=date(2026, 6, 15),
            )
        ]

        config = load_config("indmoney")
        results = fetch_all_reviews(config, WINDOW_START, WINDOW_END)

        # Should still return App Store results despite GP failure
        assert len(results) == 1
        assert results[0].source == "app_store"

    @patch("review_pulse.ingest.app_store.fetch_app_store_reviews")
    @patch("review_pulse.ingest.google_play.fetch_google_play_reviews")
    def test_handles_both_failures_gracefully(
        self, mock_gp: MagicMock, mock_as: MagicMock
    ) -> None:
        mock_gp.side_effect = Exception("GP broken")
        mock_as.side_effect = Exception("AS broken")

        config = load_config("indmoney")
        results = fetch_all_reviews(config, WINDOW_START, WINDOW_END)

        assert len(results) == 0
