"""StateGraph builder for Review Pulse pipeline using LangGraph."""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import StateGraph, START, END

from review_pulse.graph.state import PulseState
from review_pulse.graph.nodes import (
    check_idempotency,
    fetch_reviews,
    clean_reviews,
    scrub_pii,
    embed_and_cluster,
    generate_report,
    validate_quotes,
    render_report,
    deliver_report,
    audit_log,
)

logger = logging.getLogger(__name__)


def routing_decision(state: PulseState) -> Literal["fetch_reviews", "__end__"]:
    """Determine whether to proceed with pipeline or skip due to idempotency."""
    if state.get("skip", False):
        logger.info("Idempotency match: skipping pipeline execution")
        return "__end__"
    return "fetch_reviews"


def check_quotes_validation(state: PulseState) -> Literal["generate_report", "render_report"]:
    """Determine whether to retry report generation if quote validation fails."""
    val = state.get("quote_validation", {})
    valid = val.get("valid", True)
    attempts = state.get("generation_attempts", 0)

    if not valid:
        if attempts < 3:  # Max 2 retries (3 total attempts)
            logger.warning(
                "Quote validation failed (attempt %d). Retrying report generation...",
                attempts,
            )
            return "generate_report"
        else:
            logger.error(
                "Quote validation failed after %d attempts. Proceeding with valid quotes only.",
                attempts,
            )

    return "render_report"


def build_pulse_graph() -> StateGraph:
    """Build the LangGraph StateGraph workflow."""
    workflow = StateGraph(PulseState)

    # Add all nodes
    workflow.add_node("check_idempotency", check_idempotency)
    workflow.add_node("fetch_reviews", fetch_reviews)
    workflow.add_node("clean_reviews", clean_reviews)
    workflow.add_node("scrub_pii", scrub_pii)
    workflow.add_node("embed_and_cluster", embed_and_cluster)
    workflow.add_node("generate_report", generate_report)
    workflow.add_node("validate_quotes", validate_quotes)
    workflow.add_node("render_report", render_report)
    workflow.add_node("deliver_report", deliver_report)
    workflow.add_node("audit_log", audit_log)

    # Define edges and routing
    workflow.add_edge(START, "check_idempotency")

    workflow.add_conditional_edges(
        "check_idempotency",
        routing_decision,
        {
            "fetch_reviews": "fetch_reviews",
            "__end__": END,
        },
    )

    workflow.add_edge("fetch_reviews", "clean_reviews")
    workflow.add_edge("clean_reviews", "scrub_pii")
    workflow.add_edge("scrub_pii", "embed_and_cluster")
    workflow.add_edge("embed_and_cluster", "generate_report")
    workflow.add_edge("generate_report", "validate_quotes")

    workflow.add_conditional_edges(
        "validate_quotes",
        check_quotes_validation,
        {
            "generate_report": "generate_report",
            "render_report": "render_report",
        },
    )

    workflow.add_edge("render_report", "deliver_report")
    workflow.add_edge("deliver_report", "audit_log")
    workflow.add_edge("audit_log", END)

    return workflow
