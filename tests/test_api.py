"""Unit tests for the FastAPI REST API layer."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from review_pulse.api import app
from review_pulse.models import RunRecord


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def mock_repo():
    with patch("review_pulse.api.RunRepository") as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance
        yield mock_instance


# ──────────────────────────────────────────────────────────────
#  Security Tests
# ──────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"API_KEY": "supersecretkey"})
def test_api_key_unauthorized(client) -> None:
    # Requests without X-API-Key should return 401
    response = client.get("/api/runs/indmoney")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key."


@patch.dict("os.environ", {"API_KEY": "supersecretkey"})
def test_api_key_authorized(client, mock_repo) -> None:
    # Requests with valid X-API-Key should pass
    mock_repo.list_runs.return_value = []
    response = client.get("/api/runs/indmoney", headers={"X-API-Key": "supersecretkey"})
    assert response.status_code == 200


# ──────────────────────────────────────────────────────────────
#  Pipeline Trigger (POST)
# ──────────────────────────────────────────────────────────────

@patch("review_pulse.api.asyncio.get_event_loop")
def test_trigger_run_success(mock_get_loop, client, mock_repo) -> None:
    # Prevent the real event loop from actually spawning executor threads
    mock_loop = MagicMock()
    mock_get_loop.return_value = mock_loop

    mock_run = RunRecord(
        run_id="run_abc",
        product="indmoney",
        week_start=date(2026, 3, 30),
        week_end=date(2026, 4, 5),
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    mock_repo.get_run_by_product_week.return_value = None
    mock_repo.create_run.return_value = mock_run

    payload = {
        "product": "indmoney",
        "week": "2026-W14",
        "force": True,
        "dry_run": True,
    }
    response = client.post("/api/runs", json=payload)
    assert response.status_code == 202
    res_data = response.json()
    assert res_data["run_id"] == "run_abc"
    assert res_data["status"] == "running"


# ──────────────────────────────────────────────────────────────
#  Runs Query (GET)
# ──────────────────────────────────────────────────────────────

def test_list_runs(client, mock_repo) -> None:
    mock_run = RunRecord(
        run_id="run_123",
        product="indmoney",
        week_start=date(2026, 3, 30),
        week_end=date(2026, 4, 5),
        status="completed",
        reviews_fetched=100,
        reviews_processed=80,
        started_at=datetime(2026, 4, 6, 12, 0, 0),
        completed_at=datetime(2026, 4, 6, 12, 1, 0),
    )
    mock_repo.list_runs.return_value = [mock_run]

    response = client.get("/api/runs/indmoney")
    assert response.status_code == 200
    res_data = response.json()
    assert len(res_data) == 1
    assert res_data[0]["run_id"] == "run_123"
    assert res_data[0]["status"] == "completed"
    assert res_data[0]["reviews_fetched"] == 100


def test_get_run_status(client, mock_repo) -> None:
    mock_run = RunRecord(
        run_id="run_123",
        product="indmoney",
        week_start=date(2026, 3, 30),
        week_end=date(2026, 4, 5),
        status="running",
        started_at=datetime(2026, 4, 6, 12, 0, 0),
    )
    mock_repo.get_run_by_id.return_value = mock_run

    response = client.get("/api/runs/run_123/status")
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["run_id"] == "run_123"
    assert res_data["status"] == "running"


def test_get_run_status_not_found(client, mock_repo) -> None:
    mock_repo.get_run_by_id.return_value = None
    response = client.get("/api/runs/run_missing/status")
    assert response.status_code == 404
