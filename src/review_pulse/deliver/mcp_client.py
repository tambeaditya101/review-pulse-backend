"""MCP client — communicates with the external google-mcp-server.

All HTTP requests to the MCP server are routed through this module.
Each function provides:
  - Explicit payload validation (raises ValueError for empty/invalid fields)
  - Correlation IDs (Request UUID) passed via X-Request-ID header and included in all logs
  - Structured logs capturing endpoint, payload size, timeout, attempt, status, and elapsed time
  - Fine-grained exception handling for httpx request/status/timeout errors
  - Conservative retries: retries only on connection establishment failures or 502/503/504
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx

from review_pulse.config import load_settings

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_headers(api_key: str | None, request_id: str) -> dict[str, str]:
    """Return the request headers dict, including the auth key and Request ID."""
    headers: dict[str, str] = {
        "X-Request-ID": request_id,
    }
    if api_key:
        headers["X-API-Key"] = api_key
        logger.debug("[MCP] [Req ID: %s] Auth header: X-API-Key present", request_id)
    else:
        logger.debug("[MCP] [Req ID: %s] Auth header: X-API-Key absent (MCP_API_KEY not configured)", request_id)
    return headers


def _post_with_retry(
    endpoint: str,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
    request_id: str,
) -> httpx.Response | None:
    """
    POST to *url* with retry + exponential backoff.

    Returns the httpx.Response on any completed HTTP exchange,
    or None if all attempts were exhausted due to network/timeout errors.
    """
    delay = 1.0  # initial backoff in seconds
    payload_size = len(json.dumps(payload).encode("utf-8"))

    # Log initial MCP metadata
    logger.info("[MCP]\nRequest ID: %s", request_id)
    logger.info("[MCP]\nEndpoint: %s", endpoint)
    logger.info("[MCP]\nPayload size: %d bytes", payload_size)
    logger.info("[MCP]\nTimeout: %.1f seconds", timeout)

    for attempt in range(1, max_retries + 1):
        logger.info("[MCP]\nAttempt number: %d", attempt)
        start_time = time.perf_counter()

        try:
            # We configure a custom timeout to distinguish connection timeouts from read timeouts.
            # Connection timeouts can be retried safely.
            connect_timeout = min(10.0, timeout)
            read_timeout = timeout
            httpx_timeout = httpx.Timeout(timeout=timeout, connect=connect_timeout, read=read_timeout)

            logger.info("[MCP] [Req ID: %s] Calling POST %s", request_id, url)
            response = httpx.post(url, json=payload, headers=headers, timeout=httpx_timeout)
            elapsed = time.perf_counter() - start_time

            logger.info("[MCP]\nResponse status: %d", response.status_code)
            logger.info("[MCP]\nElapsed time: %.3f seconds", elapsed)

            # Retry logic for transient status codes (502, 503, 504)
            if response.status_code in (502, 503, 504):
                logger.warning(
                    "[MCP] [Req ID: %s] Transient HTTP error status %d on attempt %d/%d. Retrying...",
                    request_id,
                    response.status_code,
                    attempt,
                    max_retries,
                )
                if attempt < max_retries:
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                continue

            # Check client/server status errors. If it is 4xx or 500, raise HTTPStatusError
            # so that it gets processed by the specialized HTTPStatusError block.
            if response.is_error:
                response.raise_for_status()

            # Successful response (2xx)
            body_preview = response.text[:200].replace("\n", " ")
            logger.info("[MCP]\nResponse body (safe summary): %s", body_preview)
            return response

        except httpx.ConnectTimeout as exc:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "[MCP] [Req ID: %s] Request failed:\n"
                "- Endpoint: %s\n"
                "- Exception type: %s\n"
                "- Status code: None (Connection Timeout)\n"
                "- Elapsed time: %.3f seconds\n"
                "- Attempt number: %d",
                request_id,
                endpoint,
                type(exc).__name__,
                elapsed,
                attempt,
            )
            # Safe to retry connection timeouts (the request didn't hit the server processing stage)
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue

        except httpx.ConnectError as exc:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "[MCP] [Req ID: %s] Request failed:\n"
                "- Endpoint: %s\n"
                "- Exception type: %s\n"
                "- Status code: None (Connection Error)\n"
                "- Elapsed time: %.3f seconds\n"
                "- Attempt number: %d",
                request_id,
                endpoint,
                type(exc).__name__,
                elapsed,
                attempt,
            )
            # Safe to retry connection failures
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue

        except httpx.ReadTimeout as exc:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "[MCP] [Req ID: %s] Request failed:\n"
                "- Endpoint: %s\n"
                "- Exception type: %s\n"
                "- Status code: None (Read Timeout)\n"
                "- Elapsed time: %.3f seconds\n"
                "- Attempt number: %d",
                request_id,
                endpoint,
                type(exc).__name__,
                elapsed,
                attempt,
            )
            logger.warning(
                "[MCP] [Req ID: %s] Read timeout encountered. "
                "Retries skipped to prevent duplicate side effects on write operation.",
                request_id,
            )
            return None

        except httpx.TimeoutException as exc:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "[MCP] [Req ID: %s] Request failed:\n"
                "- Endpoint: %s\n"
                "- Exception type: %s\n"
                "- Status code: None (Timeout)\n"
                "- Elapsed time: %.3f seconds\n"
                "- Attempt number: %d",
                request_id,
                endpoint,
                type(exc).__name__,
                elapsed,
                attempt,
            )
            logger.warning(
                "[MCP] [Req ID: %s] General timeout encountered. "
                "Retries skipped to prevent duplicate side effects.",
                request_id,
            )
            return None

        except httpx.HTTPStatusError as exc:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "[MCP] [Req ID: %s] Request failed:\n"
                "- Endpoint: %s\n"
                "- Exception type: %s\n"
                "- Status code: %d\n"
                "- Elapsed time: %.3f seconds\n"
                "- Attempt number: %d",
                request_id,
                endpoint,
                type(exc).__name__,
                exc.response.status_code,
                elapsed,
                attempt,
            )
            # Do NOT retry client errors (400, 401, 403, 404) or standard internal server errors (500)
            return exc.response

        except httpx.RequestError as exc:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "[MCP] [Req ID: %s] Request failed:\n"
                "- Endpoint: %s\n"
                "- Exception type: %s\n"
                "- Status code: None\n"
                "- Elapsed time: %.3f seconds\n"
                "- Attempt number: %d",
                request_id,
                endpoint,
                type(exc).__name__,
                elapsed,
                attempt,
            )
            return None

    logger.error("[MCP] [Req ID: %s] All %d attempt(s) exhausted for %s", request_id, max_retries, url)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def deliver_doc_via_mcp(
    doc_id: str,
    content: str,
    server_url: str | None = None,
) -> bool:
    """Send a request to the MCP server to append content to a Google Doc.

    Configuration is read from the centralized Settings object.
    Raises ValueError on empty or invalid inputs.
    """
    # 1. Payload validation (no asserts)
    if not isinstance(doc_id, str) or not doc_id.strip():
        raise ValueError("doc_id must be a non-empty string")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")

    settings = load_settings()
    url = f"{server_url or settings.mcp_server_url}/append_to_doc"
    payload = {"doc_id": doc_id, "content": content}

    request_id = str(uuid.uuid4())
    headers = _build_headers(settings.mcp_api_key, request_id)

    # Log document metadata (no sensitive report content)
    logger.info("[Delivery]\nStarting Google Docs delivery")
    logger.info("[MCP] [Req ID: %s] Document ID: %s", request_id, doc_id)

    response = _post_with_retry(
        endpoint="/append_to_doc",
        url=url,
        payload=payload,
        headers=headers,
        timeout=settings.mcp_timeout_seconds,
        max_retries=settings.mcp_max_retries,
        request_id=request_id,
    )

    if response is None:
        logger.error("[MCP] [Req ID: %s] Request failed — no response received for POST /append_to_doc", request_id)
        return False

    if response.status_code == 200:
        logger.info("[Delivery]\nCompleted successfully")
        return True

    return False


def deliver_email_via_mcp(
    to: str,
    subject: str,
    body: str,
    server_url: str | None = None,
) -> bool:
    """Send a request to the MCP server to create a Gmail draft.

    Configuration is read from the centralized Settings object.
    Raises ValueError on empty or invalid inputs.
    """
    # 1. Payload validation (no asserts)
    if not isinstance(to, str) or not to.strip():
        raise ValueError("to must be a non-empty string")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("subject must be a non-empty string")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("body must be a non-empty string")

    # Calculate recipient count safely (comma separated list)
    recipient_count = len([email.strip() for email in to.split(",") if email.strip()])

    settings = load_settings()
    url = f"{server_url or settings.mcp_server_url}/create_email_draft"
    payload = {"to": to, "subject": subject, "body": body}

    request_id = str(uuid.uuid4())
    headers = _build_headers(settings.mcp_api_key, request_id)

    # Log email metadata (no sensitive report content)
    logger.info("[Delivery]\nStarting Gmail draft delivery")
    logger.info("[MCP] [Req ID: %s] Recipient count: %d", request_id, recipient_count)

    response = _post_with_retry(
        endpoint="/create_email_draft",
        url=url,
        payload=payload,
        headers=headers,
        timeout=settings.mcp_timeout_seconds,
        max_retries=settings.mcp_max_retries,
        request_id=request_id,
    )

    if response is None:
        logger.error("[MCP] [Req ID: %s] Request failed — no response received for POST /create_email_draft", request_id)
        return False

    if response.status_code == 200:
        logger.info("[Delivery]\nCompleted successfully")
        return True

    return False
