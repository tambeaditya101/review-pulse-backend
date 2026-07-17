"""FastAPI web API for Review Pulse."""

from __future__ import annotations

import os
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor

# Set a safety timeout for all network sockets
socket.setdefaulttimeout(30.0)

from fastapi import FastAPI, HTTPException, Header, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from review_pulse.config import load_settings, load_config, parse_iso_week, review_window_for_week, current_iso_week
from review_pulse.db.repository import RunRepository

logger = logging.getLogger(__name__)

# Thread pool for running blocking pipeline tasks without blocking the event loop
_executor = ThreadPoolExecutor(max_workers=2)


def _format_datetime(dt: datetime | None) -> str | None:
    """Format a datetime as ISO 8601 string."""
    if not dt:
        return None
    return dt.isoformat()


app = FastAPI(
    title="Review Pulse API",
    description="REST API for triggering and polling AI review pulse pipeline runs.",
    version="0.1.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security middleware
def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
    expected_key = os.getenv("API_KEY")
    if expected_key and x_api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


class RunPayload(BaseModel):
    product: str
    week: str | None = None
    force: bool = False
    dry_run: bool = False


def _run_pipeline_background(
    run_id: str,
    product_slug: str,
    iso_week: str,
    week_start: date,
    week_end: date,
    window_start: date,
    window_end: date,
    force: bool,
    dry_run: bool,
) -> None:
    """Invokes the compiled LangGraph pipeline graph in a background thread."""
    import traceback
    import sys

    settings = load_settings()
    repo = RunRepository(settings.database_path)

    try:
        logger.info("[BG:%s] Starting pipeline background task", run_id)
        print(f"[BG:{run_id}] Starting pipeline background task", flush=True)

        from review_pulse.graph.builder import build_pulse_graph
        logger.info("[BG:%s] Graph builder imported successfully", run_id)
        print(f"[BG:{run_id}] Graph builder imported", flush=True)

        graph = build_pulse_graph().compile()
        logger.info("[BG:%s] Graph compiled successfully", run_id)
        print(f"[BG:{run_id}] Graph compiled", flush=True)

        initial_state = {
            "run_id": run_id,
            "product_slug": product_slug,
            "iso_week": iso_week,
            "week_start": week_start,
            "week_end": week_end,
            "window_start": window_start,
            "window_end": window_end,
            "force": force,
            "dry_run": dry_run,
            "skip": False,
        }

        logger.info(
            "[BG_INVOKE] run_id=%s product_slug=%s iso_week=%s "
            "week_start=%s week_end=%s window_start=%s window_end=%s",
            run_id, product_slug, iso_week, week_start.isoformat(), week_end.isoformat(), window_start.isoformat(), window_end.isoformat()
        )

        logger.info("[BG:%s] Invoking graph...", run_id)
        print(f"[BG:{run_id}] Invoking graph...", flush=True)
        result = graph.invoke(initial_state)

        if result.get("skip", False):
            logger.info("[BG:%s] Pipeline completed: skipped (idempotency)", run_id)
            print(f"[BG:{run_id}] Skipped (idempotency)", flush=True)
        else:
            logger.info("[BG:%s] Pipeline completed successfully", run_id)
            print(f"[BG:{run_id}] Completed successfully", flush=True)

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        logger.exception("[BG:%s] Pipeline FAILED: %s", run_id, error_msg)
        print(f"[BG:{run_id}] FAILED: {error_msg}", flush=True)
        print(tb, file=sys.stderr, flush=True)
        try:
            repo.update_run_status(run_id, "failed", error_message=error_msg[:500])
        except Exception as db_exc:
            logger.error("[BG:%s] Failed to write error to DB: %s", run_id, db_exc)
            print(f"[BG:{run_id}] DB write failed: {db_exc}", flush=True)
@app.post(
    "/api/runs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_api_key)],
)
async def trigger_run(payload: RunPayload) -> dict[str, Any]:
    """Trigger a new pipeline run for a product and week."""
    settings = load_settings()
    repo = RunRepository(settings.database_path)

    # Validate product exists
    try:
        config = load_config(payload.product)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product '{payload.product}' configuration not found: {exc}",
        )

    # Resolve ISO week parameters
    iso_week = payload.week or current_iso_week()
    try:
        week_start, week_end = parse_iso_week(iso_week)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ISO week format '{iso_week}': {exc}",
        )

    window_start, window_end = review_window_for_week(week_start, settings.review_window_weeks)

    logger.info(
        "[API_TRIGGER] payload.week=%s parsed week_start=%s week_end=%s "
        "calculated window_start=%s window_end=%s",
        payload.week, week_start.isoformat(), week_end.isoformat(), window_start.isoformat(), window_end.isoformat()
    )

    # Check for active running or already completed runs (Fast API level check)
    existing = repo.get_run_by_product_week(payload.product, week_start)
    if existing and not payload.force:
        if existing.status == "completed":
            # Valid completed run check: contains processed reviews
            if existing.reviews_fetched > 0 and existing.reviews_processed > 0:
                return {
                    "run_id": existing.run_id,
                    "status": "completed",
                    "message": "A report already exists for the selected reporting week.",
                }
            else:
                # Stale completed run with 0 reviews: reset and reuse it safely
                logger.info("Reusing stale completed run: %s", existing.run_id)
                repo.reset_run(existing.run_id, datetime.now(timezone.utc))
                run_rec = existing
        elif existing.status == "running":
            started_at = existing.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - started_at
            if delta.total_seconds() <= 3600:
                return {
                    "run_id": existing.run_id,
                    "status": "running",
                    "message": f"Another execution is already active (started {delta.total_seconds() / 60:.1f}m ago).",
                }
            else:
                # Stale running execution: reset and reuse it safely
                logger.info("Reusing stale running run: %s", existing.run_id)
                repo.reset_run(existing.run_id, datetime.now(timezone.utc))
                run_rec = existing
        else:
            # Failed or other statuses: reset and reuse it safely
            logger.info("Reusing failed/stale run record: %s", existing.run_id)
            repo.reset_run(existing.run_id, datetime.now(timezone.utc))
            run_rec = existing
    else:
        # If payload.force is True and existing exists, we must reset and reuse it to avoid UNIQUE constraint violation.
        if existing:
            logger.info("Force run requested: resetting existing run record %s", existing.run_id)
            repo.reset_run(existing.run_id, datetime.now(timezone.utc))
            run_rec = existing
        else:
            # Pre-register a new run as running
            run_rec = repo.create_run(
                product=payload.product,
                week_start=week_start,
                week_end=week_end,
                status="running",
            )

    # Run the pipeline in a thread pool — keeps the event loop free so health checks pass
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        _executor,
        _run_pipeline_background,
        run_rec.run_id,
        payload.product,
        iso_week,
        week_start,
        week_end,
        window_start,
        window_end,
        payload.force,
        payload.dry_run,
    )

    return {
        "run_id": run_rec.run_id,
        "status": "running",
        "product": payload.product,
        "week": iso_week,
    }


@app.get("/api/runs/{product}", dependencies=[Depends(verify_api_key)])
def list_runs(product: str, limit: int = 5) -> list[dict[str, Any]]:
    """List the run history metrics for a product."""
    settings = load_settings()
    repo = RunRepository(settings.database_path)

    runs = repo.list_runs(product, limit=limit)
    return [
        {
            "run_id": r.run_id,
            "product": r.product,
            "week_start": r.week_start.isoformat(),
            "week_end": r.week_end.isoformat(),
            "status": r.status,
            "reviews_fetched": r.reviews_fetched,
            "reviews_processed": r.reviews_processed,
            "report_path": r.report_path,
            "google_doc_id": r.google_doc_id,
            "email_sent": r.email_sent,
            "groq_tokens_used": r.groq_tokens_used,
            "error_message": r.error_message,
            "started_at": _format_datetime(r.started_at),
            "completed_at": _format_datetime(r.completed_at),
        }
        for r in runs
    ]


@app.get("/api/runs/{run_id}/status", dependencies=[Depends(verify_api_key)])
def get_run_status(run_id: str) -> dict[str, Any]:
    """Check the processing status of a run by ID."""
    settings = load_settings()
    repo = RunRepository(settings.database_path)

    run = repo.get_run_by_id(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    return {
        "run_id": run.run_id,
        "status": run.status,
        "reviews_fetched": run.reviews_fetched,
        "reviews_processed": run.reviews_processed,
        "error_message": run.error_message,
        "started_at": _format_datetime(run.started_at),
        "completed_at": _format_datetime(run.completed_at),
    }


@app.get("/api/runs/{run_id}/report", dependencies=[Depends(verify_api_key)])
def get_run_report(run_id: str) -> dict[str, Any]:
    """Retrieve the generated markdown report content for a completed run."""
    settings = load_settings()
    repo = RunRepository(settings.database_path)

    run = repo.get_run_by_id(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    if run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run '{run_id}' is in status '{run.status}'. Report is not ready.",
        )

    if not run.report_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report path not specified for run.",
        )

    report_file_path = Path(run.report_path)
    if not report_file_path.is_absolute():
        report_file_path = settings.database_path.parent / report_file_path

    if not report_file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report markdown file not found on disk.",
        )

    try:
        report_content = report_file_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read report file: {exc}",
        )

    return {
        "run_id": run.run_id,
        "product": run.product,
        "week_start": run.week_start.isoformat(),
        "report_path": run.report_path,
        "content": report_content,
    }


@app.get("/api/themes/{run_id}", dependencies=[Depends(verify_api_key)])
def get_run_themes(run_id: str) -> list[dict[str, Any]]:
    """Retrieve the LLM-enriched themes generated for a run."""
    settings = load_settings()
    repo = RunRepository(settings.database_path)

    run = repo.get_run_by_id(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    return repo.get_themes_for_run(run_id)


@app.get("/api/debug/mcp")
def debug_mcp_connection() -> dict[str, Any]:
    """Diagnostic endpoint to inspect MCP configuration and test connectivity.
    
    WARNING: This diagnostic endpoint currently performs a write operation 
    (POST /append_to_doc with a test ping payload) to verify connectivity. 
    This should eventually be replaced by a non-destructive health endpoint 
    (e.g. GET /health or a dedicated ping/probe route) once the MCP server 
    exposes one. Do not modify the existing endpoint to use new routes until 
    the MCP API contract supports them.
    """
    import httpx
    settings = load_settings()
    
    mcp_url = settings.mcp_server_url
    mcp_key = os.getenv("MCP_API_KEY")
    doc_id = settings.google_doc_id
    
    results = {
        "mcp_server_url": mcp_url,
        "mcp_api_key_status": "present" if mcp_key else "missing",
        "google_doc_id_status": "present" if doc_id else "missing",
        "google_doc_id_preview": f"{doc_id[:8]}..." if doc_id else None,
        "connection_test": "not_started"
    }
    
    try:
        headers = {}
        if mcp_key:
            headers["X-API-Key"] = mcp_key
            
        # Send a test dummy request to see if we get a response (expect 403 or 401 if wrong, or 500, but not connection error)
        response = httpx.post(
            f"{mcp_url}/append_to_doc", 
            json={"doc_id": "test_ping_id", "content": "ping_test"}, 
            headers=headers, 
            timeout=8.0
        )
        results["connection_test"] = {
            "status_code": response.status_code,
            "response_preview": response.text[:200]
        }
    except Exception as exc:
        results["connection_test"] = {
            "error_type": type(exc).__name__,
            "error_message": str(exc)
        }
        
    return results
