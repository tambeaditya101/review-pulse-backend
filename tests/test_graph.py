"""Integration tests for the LangGraph state machine pipeline."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from review_pulse.config import load_config
from review_pulse.graph.builder import build_pulse_graph
from review_pulse.models import Review


@pytest.fixture
def mock_scrapers():
    """Mock the scraper functions to return static reviews."""
    gp_review = Review(
        review_id="gp1",
        source="google_play",
        text="The mutual fund tracking is extremely smooth.",
        rating=5,
        review_date=date(2026, 6, 15),
    )
    as_review = Review(
        review_id="as1",
        source="app_store",
        text="Redemption takes too long. Please fix.",
        rating=2,
        review_date=date(2026, 6, 16),
    )
    with patch("review_pulse.ingest.google_play.fetch_google_play_reviews", return_value=[gp_review]), \
         patch("review_pulse.ingest.app_store.fetch_app_store_reviews", return_value=[as_review]):
        yield


@pytest.fixture
def mock_groq():
    """Mock Groq client response to return structured JSON."""
    mock_response = {
        "summary": "Overall positive sentiment regarding mutual fund features but complaints about redemption delays.",
        "themes": [
            {
                "label": "Mutual Fund Tracking",
                "description": "Smooth experience tracking mutual funds.",
                "review_count": 1,
                "avg_rating": 5.0
            },
            {
                "label": "Redemption Process",
                "description": "Complaints about redemption delay.",
                "review_count": 1,
                "avg_rating": 2.0
            }
        ],
        "quotes": [
            {
                "text": "mutual fund tracking is extremely smooth.",
                "rating": 5,
                "source": "google_play",
                "theme_label": "Mutual Fund Tracking"
            },
            {
                "text": "Redemption takes too long. Please fix.",
                "rating": 2,
                "source": "app_store",
                "theme_label": "Redemption Process"
            }
        ],
        "action_ideas": [
            "Optimize redemption transaction backend to reduce processing time."
        ]
    }
    with patch("review_pulse.graph.nodes.generate_report_json", return_value=(mock_response, 1200)) as mock_call:
        yield mock_call


def test_full_graph_execution(mock_scrapers, mock_groq, tmp_path) -> None:
    """Test that the full compiled graph runs end-to-end with mocked external calls."""
    # Override settings path to use a temp database
    temp_db_path = tmp_path / "test_run.db"

    with patch("review_pulse.graph.nodes.load_settings") as mock_settings_nodes:
        # Let's mock load_settings to return database_path pointing to temp_db_path
        settings_mock = MagicMock()
        settings_mock.database_path = temp_db_path
        settings_mock.require_groq_api_key.return_value = "mock_key"
        settings_mock.groq_model = "llama-3.3-70b-versatile"
        settings_mock.review_window_weeks = 10
        mock_settings_nodes.return_value = settings_mock

        # Let's import repository inside test context to connect to temp_db
        from review_pulse.db.repository import RunRepository
        real_repo = RunRepository(temp_db_path)

        # Build graph
        graph = build_pulse_graph().compile()

        # Define initial state
        initial_state = {
            "product_slug": "indmoney",
            "iso_week": "2026-W25",
            "week_start": date(2026, 6, 15),
            "week_end": date(2026, 6, 21),
            "window_start": date(2026, 4, 13),
            "window_end": date(2026, 6, 21),
            "force": True,
            "dry_run": True,
            "skip": False,
        }

        # Run pipeline
        result = graph.invoke(initial_state)

        # Assertions
        assert not result.get("skip")
        assert result.get("run_id") is not None

        # Verify metrics
        metrics = result.get("metrics")
        assert metrics is not None
        assert metrics.reviews_fetched == 2
        assert metrics.reviews_processed == 2
        assert metrics.themes_found == 1  # Standard k-means fallback to k=1 for tiny dataset
        assert metrics.groq_tokens_used == 1200

        # Verify database record
        run_record = real_repo.get_run_by_product_week("indmoney", date(2026, 6, 15))
        assert run_record is not None
        assert run_record.status == "completed"
        assert run_record.reviews_fetched == 2
        assert run_record.reviews_processed == 2
        assert run_record.groq_tokens_used == 1200
