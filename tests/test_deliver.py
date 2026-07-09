"""Unit tests for the Google Workspace MCP client delivery system."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import httpx

from review_pulse.models import ReportDraft, ReportTheme
from review_pulse.deliver.mcp_client import (
    deliver_doc_via_mcp,
    deliver_email_via_mcp,
)


@pytest.fixture
def mock_httpx_post():
    with patch("httpx.post") as mock_post:
        yield mock_post


# ──────────────────────────────────────────────────────────────
#  MCP Client HTTP Requests
# ──────────────────────────────────────────────────────────────

def test_deliver_doc_via_mcp_success(mock_httpx_post) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_post.return_value = mock_response

    success = deliver_doc_via_mcp("doc_123", "Markdown Content")
    assert success
    mock_httpx_post.assert_called_once_with(
        "http://127.0.0.1:8000/append_to_doc",
        json={"doc_id": "doc_123", "content": "Markdown Content"},
        timeout=120.0,
    )


def test_deliver_doc_via_mcp_failure(mock_httpx_post) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Rejected by user"
    mock_httpx_post.return_value = mock_response

    success = deliver_doc_via_mcp("doc_123", "Markdown Content")
    assert not success


def test_deliver_email_via_mcp_success(mock_httpx_post) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_post.return_value = mock_response

    success = deliver_email_via_mcp("user@test.com", "Subject", "Body HTML")
    assert success
    mock_httpx_post.assert_called_once_with(
        "http://127.0.0.1:8000/create_email_draft",
        json={"to": "user@test.com", "subject": "Subject", "body": "Body HTML"},
        timeout=120.0,
    )


# ──────────────────────────────────────────────────────────────
#  deliver_report Graph Node Integration
# ──────────────────────────────────────────────────────────────

@patch("review_pulse.graph.nodes.load_settings")
def test_deliver_report_node_dry_run(mock_settings) -> None:
    """If dry_run is True, bypass all delivery and return None/False."""
    from review_pulse.graph.nodes import deliver_report
    
    state = {
        "dry_run": True,
        "skip": False,
    }

    result = deliver_report(state)
    assert result == {"google_doc_id": None, "email_sent": False}


@patch("review_pulse.graph.nodes.load_settings")
def test_deliver_report_node_no_doc_id(mock_settings, tmp_path) -> None:
    """If no google_doc_id is configured or found, skip Google Doc append."""
    from review_pulse.graph.nodes import deliver_report

    mock_sett = MagicMock()
    mock_sett.google_doc_id = ""
    mock_sett.database_path = tmp_path / "test.db"
    mock_settings.return_value = mock_sett

    state = {
        "product_slug": "indmoney",
        "week_start": date(2026, 3, 30),
        "dry_run": False,
        "skip": False,
    }

    # Should run cleanly and return None for google_doc_id
    result = deliver_report(state)
    assert result["google_doc_id"] is None
    assert not result["email_sent"]
