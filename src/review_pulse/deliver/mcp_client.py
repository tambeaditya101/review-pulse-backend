"""MCP client — communicates with the external google-mcp-server."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


def deliver_doc_via_mcp(
    doc_id: str,
    content: str,
    server_url: str = _DEFAULT_SERVER_URL,
) -> bool:
    """Send a request to the MCP server to append content to a Google Doc."""
    url = f"{server_url}/append_to_doc"
    payload = {
        "doc_id": doc_id,
        "content": content,
    }

    try:
        logger.info("Sending append request to MCP server: %s", url)
        # Set a long timeout since the server operator needs to approve via terminal
        response = httpx.post(url, json=payload, timeout=120.0)
        
        if response.status_code == 200:
            logger.info("Successfully appended to Google Doc via MCP server.")
            return True
        else:
            logger.error(
                "MCP server returned error %d: %s",
                response.status_code,
                response.text,
            )
            return False
    except Exception as exc:
        logger.error("Failed to connect to MCP server: %s", exc)
        return False


def deliver_email_via_mcp(
    to: str,
    subject: str,
    body: str,
    server_url: str = _DEFAULT_SERVER_URL,
) -> bool:
    """Send a request to the MCP server to create a Gmail draft."""
    url = f"{server_url}/create_email_draft"
    payload = {
        "to": to,
        "subject": subject,
        "body": body,
    }

    try:
        logger.info("Sending email draft request to MCP server: %s", url)
        # Set a long timeout since the server operator needs to approve via terminal
        response = httpx.post(url, json=payload, timeout=120.0)
        
        if response.status_code == 200:
            logger.info("Successfully created Gmail draft via MCP server.")
            return True
        else:
            logger.error(
                "MCP server returned error %d: %s",
                response.status_code,
                response.text,
            )
            return False
    except Exception as exc:
        logger.error("Failed to connect to MCP server: %s", exc)
        return False
