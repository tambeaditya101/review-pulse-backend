"""FastAPI web API for Review Pulse."""

from __future__ import annotations

import os
import logging
from datetime import date
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
    "/api/debug/run",
    dependencies=[Depends(verify_api_key)],
)
def debug_run_sync(payload: RunPayload) -> dict[str, Any]:
    """DEBUG: Run the pipeline synchronously to capture errors directly.

    Returns the full error traceback in the HTTP response if it fails.
    """
    import traceback

    settings = load_settings()
    repo = RunRepository(settings.database_path)

    try:
        config = load_config(payload.product)
    except Exception as exc:
        return {"error": f"Config load failed: {exc}"}

    iso_week = payload.week or current_iso_week()
    try:
        week_start, week_end = parse_iso_week(iso_week)
    except ValueError as exc:
        return {"error": f"Invalid week: {exc}"}

    window_start, window_end = review_window_for_week(week_start, settings.review_window_weeks)

    steps_completed = []
    try:
        steps_completed.append("settings_loaded")

        from review_pulse.graph.builder import build_pulse_graph
        steps_completed.append("builder_imported")

        graph = build_pulse_graph().compile()
        steps_completed.append("graph_compiled")

        initial_state = {
            "product_slug": payload.product,
            "iso_week": iso_week,
            "week_start": week_start,
            "week_end": week_end,
            "window_start": window_start,
            "window_end": window_end,
            "force": payload.force,
            "dry_run": payload.dry_run,
            "skip": False,
        }

        result = graph.invoke(initial_state)
        steps_completed.append("graph_invoked")

        return {
            "status": "success",
            "steps_completed": steps_completed,
            "skip": result.get("skip", False),
            "run_id": result.get("run_id"),
        }

    except Exception as exc:
        return {
            "status": "failed",
            "steps_completed": steps_completed,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


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

    # Check for active running or already completed runs (Fast API level check)
    existing = repo.get_run_by_product_week(payload.product, week_start)
    if existing and not payload.force:
        if existing.status == "completed":
            return {
                "run_id": existing.run_id,
                "status": "completed",
                "message": "Run already completed for this week. Use force=true to override.",
            }
        elif existing.status == "running":
            import datetime
            delta = datetime.datetime.now() - existing.started_at
            if delta.total_seconds() <= 3600:
                return {
                    "run_id": existing.run_id,
                    "status": "running",
                    "message": f"Another execution is already active (started {delta.total_seconds() / 60:.1f}m ago).",
                }

    # Pre-register the run as running to obtain a stable run_id
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
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
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
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
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

    if not run.report_path or not Path(run.report_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report markdown file not found on disk.",
        )

    try:
        report_content = Path(run.report_path).read_text(encoding="utf-8")
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
