"""Unit tests for the Google Workspace MCP client delivery system."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest
import httpx
import uuid

from review_pulse.models import ReportDraft, ReportTheme
from review_pulse.deliver.mcp_client import (
    deliver_doc_via_mcp,
    deliver_email_via_mcp,
)


# ──────────────────────────────────────────────────────────────
#  Shared fixtures & Helpers
# ──────────────────────────────────────────────────────────────

def _make_settings(
    mcp_server_url: str = "http://127.0.0.1:8000",
    mcp_api_key: str | None = None,
    mcp_timeout_seconds: float = 60.0,
    mcp_max_retries: int = 1,  # single attempt default in tests
) -> MagicMock:
    s = MagicMock()
    s.mcp_server_url = mcp_server_url
    s.mcp_api_key = mcp_api_key
    s.mcp_timeout_seconds = mcp_timeout_seconds
    s.mcp_max_retries = mcp_max_retries
    return s


@pytest.fixture
def mock_httpx_post():
    with patch("review_pulse.deliver.mcp_client.httpx.post") as mock_post:
        yield mock_post


@pytest.fixture
def mock_settings_no_key():
    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(),
    ):
        yield


@pytest.fixture
def mock_settings_with_key():
    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(mcp_api_key="secret_mcp_key"),
    ):
        yield


# ──────────────────────────────────────────────────────────────
#  Payload Validation Tests (P1 - Explicit ValueError)
# ──────────────────────────────────────────────────────────────

def test_deliver_doc_validation_empty_doc_id() -> None:
    with pytest.raises(ValueError, match="doc_id must be a non-empty string"):
        deliver_doc_via_mcp(" ", "content")


def test_deliver_doc_validation_empty_content() -> None:
    with pytest.raises(ValueError, match="content must be a non-empty string"):
        deliver_doc_via_mcp("doc-123", "")


def test_deliver_email_validation_empty_to() -> None:
    with pytest.raises(ValueError, match="to must be a non-empty string"):
        deliver_email_via_mcp("", "subject", "body")


def test_deliver_email_validation_empty_subject() -> None:
    with pytest.raises(ValueError, match="subject must be a non-empty string"):
        deliver_email_via_mcp("test@test.com", " ", "body")


def test_deliver_email_validation_empty_body() -> None:
    with pytest.raises(ValueError, match="body must be a non-empty string"):
        deliver_email_via_mcp("test@test.com", "subject", "")


# ──────────────────────────────────────────────────────────────
#  X-Request-ID and Request Correlation Tests
# ──────────────────────────────────────────────────────────────

def test_deliver_doc_correlation_id_sent(mock_httpx_post, mock_settings_no_key) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "success"
    mock_response.is_error = False
    mock_httpx_post.return_value = mock_response

    success = deliver_doc_via_mcp("doc_123", "Markdown Content")
    assert success

    # Verify httpx.post was called with custom X-Request-ID header
    args, kwargs = mock_httpx_post.call_args
    headers = kwargs["headers"]
    assert "X-Request-ID" in headers
    # Verify it is a valid UUID
    assert uuid.UUID(headers["X-Request-ID"])


# ──────────────────────────────────────────────────────────────
#  deliver_doc_via_mcp — success paths
# ──────────────────────────────────────────────────────────────

def test_deliver_doc_via_mcp_success(mock_httpx_post, mock_settings_no_key) -> None:
    """Returns True on HTTP 200; sends correct URL and payload; no auth header without key."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.is_error = False
    mock_httpx_post.return_value = mock_response

    success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert success
    mock_httpx_post.assert_called_once()
    args, kwargs = mock_httpx_post.call_args
    assert args[0] == "http://127.0.0.1:8000/append_to_doc"
    assert kwargs["json"] == {"doc_id": "doc_123", "content": "Markdown Content"}
    assert "X-API-Key" not in kwargs["headers"]


def test_deliver_doc_via_mcp_with_api_key(mock_httpx_post, mock_settings_with_key) -> None:
    """X-API-Key header is included when mcp_api_key is configured."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.is_error = False
    mock_httpx_post.return_value = mock_response

    success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert success
    mock_httpx_post.assert_called_once()
    args, kwargs = mock_httpx_post.call_args
    assert kwargs["headers"]["X-API-Key"] == "secret_mcp_key"


# ──────────────────────────────────────────────────────────────
#  deliver_doc_via_mcp — failure & retry paths (Conservative Retries)
# ──────────────────────────────────────────────────────────────

def test_deliver_doc_via_mcp_http_status_error_no_retry(mock_httpx_post, mock_settings_no_key) -> None:
    """Returns False immediately (no retries) on client status errors like 403."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Rejected by user"
    mock_response.is_error = True
    # mock raise_for_status behaviour
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Forbidden", request=MagicMock(), response=mock_response
    )
    mock_httpx_post.return_value = mock_response

    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(mcp_max_retries=3),
    ):
        success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert not success
    assert mock_httpx_post.call_count == 1  # No retries on 403


def test_deliver_doc_via_mcp_retry_on_502(mock_httpx_post) -> None:
    """Retries up to max_retries times when receiving transient server errors (502)."""
    bad_response = MagicMock()
    bad_response.status_code = 502
    bad_response.text = "Bad Gateway"
    bad_response.is_error = True
    mock_httpx_post.return_value = bad_response

    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(mcp_max_retries=3),
    ), patch("review_pulse.deliver.mcp_client.time.sleep"):
        success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert not success
    assert mock_httpx_post.call_count == 3  # Retry on 502


def test_deliver_doc_via_mcp_read_timeout_no_retry(mock_httpx_post) -> None:
    """Does NOT retry on ReadTimeout to prevent side-effects on write operations."""
    mock_httpx_post.side_effect = httpx.ReadTimeout("read timed out")

    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(mcp_max_retries=3),
    ):
        success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert not success
    assert mock_httpx_post.call_count == 1  # No retries on ReadTimeout


def test_deliver_doc_via_mcp_connect_timeout_retry(mock_httpx_post) -> None:
    """Retries on ConnectTimeout as it is safe (request has not reached the server)."""
    mock_httpx_post.side_effect = httpx.ConnectTimeout("connect timed out")

    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(mcp_max_retries=3),
    ), patch("review_pulse.deliver.mcp_client.time.sleep"):
        success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert not success
    assert mock_httpx_post.call_count == 3  # Retries on ConnectTimeout


def test_deliver_doc_via_mcp_connect_error_retry(mock_httpx_post) -> None:
    """Retries on ConnectError as it is safe."""
    mock_httpx_post.side_effect = httpx.ConnectError("connection refused")

    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(mcp_max_retries=3),
    ), patch("review_pulse.deliver.mcp_client.time.sleep"):
        success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert not success
    assert mock_httpx_post.call_count == 3  # Retries on ConnectError


def test_deliver_doc_via_mcp_retries_then_succeeds(mock_httpx_post) -> None:
    """Retries on connection failures and returns True if a subsequent attempt succeeds."""
    good_response = MagicMock()
    good_response.status_code = 200
    good_response.text = ""
    good_response.is_error = False

    mock_httpx_post.side_effect = [
        httpx.ConnectError("transient error"),
        good_response,
    ]

    with patch(
        "review_pulse.deliver.mcp_client.load_settings",
        return_value=_make_settings(mcp_max_retries=3),
    ), patch("review_pulse.deliver.mcp_client.time.sleep"):
        success = deliver_doc_via_mcp("doc_123", "Markdown Content")

    assert success
    assert mock_httpx_post.call_count == 2


# ──────────────────────────────────────────────────────────────
#  deliver_email_via_mcp — success paths
# ──────────────────────────────────────────────────────────────

def test_deliver_email_via_mcp_success(mock_httpx_post, mock_settings_no_key) -> None:
    """Returns True on HTTP 200; sends correct URL and payload; no auth header without key."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.is_error = False
    mock_httpx_post.return_value = mock_response

    success = deliver_email_via_mcp("user@test.com", "Subject", "Body HTML")

    assert success
    mock_httpx_post.assert_called_once()
    args, kwargs = mock_httpx_post.call_args
    assert args[0] == "http://127.0.0.1:8000/create_email_draft"
    assert kwargs["json"] == {"to": "user@test.com", "subject": "Subject", "body": "Body HTML"}


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

    result = deliver_report(state)
    assert result["google_doc_id"] is None
    assert not result["email_sent"]
