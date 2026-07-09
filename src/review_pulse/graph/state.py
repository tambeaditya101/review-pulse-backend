"""PulseState — the shared state dict flowing through the LangGraph pipeline."""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from review_pulse.models import (
    ReportDraft,
    Review,
    RunMetrics,
    RunRecord,
    ThemeCluster,
)


class PulseState(TypedDict, total=False):
    """State flowing through every node in the review-pulse graph.

    Fields are populated progressively as nodes execute.
    All fields are optional (total=False) so each node only writes
    what it's responsible for.
    """

    # ── Config / context ──────────────────────────────────────
    product_slug: str
    iso_week: str
    week_start: date
    week_end: date
    window_start: date
    window_end: date
    force: bool
    dry_run: bool

    # ── Run tracking ──────────────────────────────────────────
    run_id: str
    run_record: RunRecord
    skip: bool  # Set by idempotency check

    # ── Ingestion (P1) ────────────────────────────────────────
    raw_reviews: list[Review]
    clean_reviews: list[Review]

    # ── Processing (P2) ───────────────────────────────────────
    scrubbed_reviews: list[Review]
    themes: list[ThemeCluster]

    # ── LLM (P3) ─────────────────────────────────────────────
    report_draft: ReportDraft
    report_json: dict[str, Any]
    quote_validation: dict[str, Any]
    generation_attempts: int

    # ── Rendering (P4 stub) ───────────────────────────────────
    report_path: str | None

    # ── Delivery (P5 stub) ────────────────────────────────────
    google_doc_id: str | None
    email_sent: bool

    # ── Metrics ───────────────────────────────────────────────
    metrics: RunMetrics
    error_message: str | None
