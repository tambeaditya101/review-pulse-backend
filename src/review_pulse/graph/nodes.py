"""LangGraph nodes for the review-pulse pipeline."""

from __future__ import annotations

import logging
from datetime import date, datetime
import time

from review_pulse.config import load_config, load_settings
from review_pulse.db.repository import RunRepository
from review_pulse.graph.state import PulseState
from review_pulse.ingest import fetch_all_reviews
from review_pulse.models import ReportDraft, ReportTheme, ReportQuote, RunMetrics, RunRecord, Review
from review_pulse.process.clean import clean_and_deduplicate
from review_pulse.process.pii import scrub_reviews
from review_pulse.process.cluster import cluster_reviews
from review_pulse.llm.prompts import build_user_prompt
from review_pulse.llm.groq_client import generate_report_json
from review_pulse.validate.quotes import validate_all_quotes

logger = logging.getLogger(__name__)


def check_idempotency(state: PulseState) -> PulseState:
    """Check if the weekly run has already completed successfully."""
    logger.info("Node [check_idempotency] starting")
    settings = load_settings()
    repo = RunRepository(settings.database_path)

    existing = repo.get_run_by_product_week(state["product_slug"], state["week_start"])
    if existing and not state.get("force", False):
        if existing.status == "completed":
            logger.info("Run already completed. Skipping.")
            return {"skip": True}
        elif existing.status == "running":
            # If the active run has the same run_id as state["run_id"], it is our current API-spawned execution.
            if state.get("run_id") == existing.run_id:
                logger.info("Reusing active run ID: %s", existing.run_id)
            else:
                # Check if stale (older than 1 hour)
                delta = datetime.now() - existing.started_at
                if delta.total_seconds() > 3600:
                    logger.warning(
                        "Found stale running execution from %s (started %s ago). Re-running/recovering.",
                        existing.started_at,
                        delta,
                    )
                else:
                    logger.warning(
                        "Another run is currently active for this product/week (started %s ago). "
                        "Skipping to prevent concurrent conflict.",
                        delta,
                    )
                    return {"skip": True}

    # Initialize run record in database as running
    run_rec = existing
    if not run_rec:
        run_rec = repo.create_run(
            product=state["product_slug"],
            week_start=state["week_start"],
            week_end=state["week_end"],
            status="running",
        )
    else:
        repo.update_run_status(run_rec.run_id, "running")
        run_rec.status = "running"
        run_rec.started_at = datetime.now()

    metrics = state.get("metrics") or RunMetrics()

    return {
        "run_id": run_rec.run_id,
        "run_record": run_rec,
        "skip": False,
        "metrics": metrics,
        "generation_attempts": 0,
    }


def fetch_reviews(state: PulseState) -> PulseState:
    """Fetch raw reviews from Google Play and Apple App Store."""
    if state.get("skip", False):
        return {}

    logger.info("Node [fetch_reviews] starting")
    config = load_config(state["product_slug"])

    raw = fetch_all_reviews(
        config=config,
        window_start=state["window_start"],
        window_end=state["window_end"],
    )

    # Count reviews strictly belonging to the target week for the dashboard metrics
    week_start = state["week_start"]
    week_end = state["week_end"]
    fetched_count = sum(1 for r in raw if week_start <= r.review_date <= week_end)

    metrics = state.get("metrics") or RunMetrics()
    metrics.reviews_fetched = fetched_count

    return {
        "raw_reviews": raw,
        "metrics": metrics,
    }


def clean_reviews(state: PulseState) -> PulseState:
    """Clean and deduplicate the fetched reviews."""
    if state.get("skip", False):
        return {}

    logger.info("Node [clean_reviews] starting")
    raw = state.get("raw_reviews", [])

    cleaned = clean_and_deduplicate(
        reviews=raw,
        window_start=state["window_start"],
        window_end=state["window_end"],
    )

    # Count reviews strictly belonging to the target week for the dashboard metrics
    week_start = state["week_start"]
    week_end = state["week_end"]
    processed_count = sum(1 for r in cleaned if week_start <= r.review_date <= week_end)

    metrics = state.get("metrics") or RunMetrics()
    metrics.reviews_processed = processed_count

    return {
        "clean_reviews": cleaned,
        "metrics": metrics,
    }


def scrub_pii(state: PulseState) -> PulseState:
    """Scrub personal identifying information (PII) from reviews."""
    if state.get("skip", False):
        return {}

    logger.info("Node [scrub_pii] starting")
    clean = state.get("clean_reviews", [])

    # We make a copy of reviews to avoid modifying original state elements in-place unexpectedly
    scrubbed = []
    for r in clean:
        scrubbed.append(
            Review(
                review_id=r.review_id,
                source=r.source,
                text=r.text,
                rating=r.rating,
                review_date=r.review_date,
                title=r.title,
                author=r.author,
                app_version=r.app_version,
                fetched_at=r.fetched_at,
            )
        )

    scrub_reviews(scrubbed)

    return {
        "scrubbed_reviews": scrubbed,
    }


def embed_and_cluster(state: PulseState) -> PulseState:
    """Generate local sentence embeddings and cluster them into themes."""
    if state.get("skip", False):
        return {}

    logger.info("Node [embed_and_cluster] starting")
    reviews = state.get("scrubbed_reviews", [])
    run_id = state.get("run_id")

    themes = cluster_reviews(reviews, run_id=run_id)

    metrics = state.get("metrics") or RunMetrics()
    metrics.themes_found = len(themes)

    return {
        "themes": themes,
        "metrics": metrics,
    }


def _sample_reviews_for_llm(reviews: list[Review], max_per_cluster: int = 8, max_chars: int = 250) -> list[Review]:
    """Sample up to max_per_cluster reviews from each cluster and truncate text to avoid exceeding LLM rate limits."""
    import collections
    by_cluster = collections.defaultdict(list)
    for r in reviews:
        cid = getattr(r, "cluster_id", 0)
        by_cluster[cid].append(r)

    sampled = []
    for cid, cluster_revs in by_cluster.items():
        # Sort by length descending to prioritize reviews with some substance over very short ones
        sorted_revs = sorted(cluster_revs, key=lambda x: len(x.text), reverse=True)
        for r in sorted_revs[:max_per_cluster]:
            text = r.text
            if len(text) > max_chars:
                text = text[:max_chars] + "..."
            # Create a copy with truncated text
            truncated_review = Review(
                review_id=r.review_id,
                source=r.source,
                text=text,
                rating=r.rating,
                review_date=r.review_date,
                title=r.title,
                author=r.author,
                app_version=r.app_version,
                fetched_at=r.fetched_at,
            )
            # Retain cluster_id
            truncated_review.cluster_id = cid  # type: ignore[attr-defined]
            sampled.append(truncated_review)
    return sampled


def generate_report(state: PulseState) -> PulseState:
    """Generate the structured report draft using Groq LLM."""
    if state.get("skip", False):
        return {}

    logger.info("Node [generate_report] starting")
    settings = load_settings()
    config = load_config(state["product_slug"])

    reviews = state.get("scrubbed_reviews", [])
    themes = state.get("themes", [])
    attempts = state.get("generation_attempts", 0) + 1

    api_key = settings.require_groq_api_key()

    # Sample reviews to avoid hitting token per minute rate limits
    sampled_reviews = _sample_reviews_for_llm(reviews, max_per_cluster=8)
    logger.info("Sampled %d reviews for LLM prompt out of %d total", len(sampled_reviews), len(reviews))

    prompt = build_user_prompt(
        product_name=config.product.display_name,
        iso_week=state["iso_week"],
        window_start=state["window_start"].isoformat(),
        window_end=state["window_end"].isoformat(),
        week_start=state["week_start"].isoformat(),
        week_end=state["week_end"].isoformat(),
        reviews=sampled_reviews,
        themes=themes,
        max_themes=config.report.max_themes,
        max_quotes=config.report.max_themes * config.report.max_quotes_per_theme,
        max_action_ideas=config.report.max_action_ideas,
    )

    # Call LLM
    report_json, tokens_used = generate_report_json(
        api_key=api_key,
        user_prompt=prompt,
        model=settings.groq_model,
    )

    # Map LLM theme descriptions/labels back to cluster objects
    updated_themes = []
    theme_mapping = {}
    for t_json in report_json.get("themes", []):
        raw_cid = t_json.get("cluster_id")
        if raw_cid is not None:
            try:
                if isinstance(raw_cid, str):
                    import re
                    match = re.search(r'\d+', raw_cid)
                    cid = int(match.group()) - 1 if match else -1
                else:
                    cid = int(raw_cid) - 1
                if cid >= 0:
                    theme_mapping[cid] = (t_json.get("label", ""), t_json.get("description", ""))
            except Exception:
                pass

    for theme in themes:
        if theme.cluster_id in theme_mapping:
            lbl, desc = theme_mapping[theme.cluster_id]
            theme.label = lbl.strip()
            theme.description = desc.strip()
        updated_themes.append(theme)

    metrics = state.get("metrics") or RunMetrics()
    metrics.groq_tokens_used += tokens_used

    return {
        "report_json": report_json,
        "themes": updated_themes,
        "generation_attempts": attempts,
        "metrics": metrics,
    }


def validate_quotes(state: PulseState) -> PulseState:
    """Validate that LLM report quotes fuzzy-match source reviews."""
    if state.get("skip", False):
        return {}

    logger.info("Node [validate_quotes] starting")
    report_json = state.get("report_json", {})
    reviews = state.get("scrubbed_reviews", [])

    quotes = report_json.get("quotes", [])
    validation_res = validate_all_quotes(quotes, reviews)

    metrics = state.get("metrics") or RunMetrics()
    metrics.quotes_validated = validation_res.passed_quotes
    metrics.quotes_dropped = len(validation_res.failed_quotes)

    return {
        "quote_validation": {
            "valid": validation_res.valid,
            "failed_quotes": validation_res.failed_quotes,
        },
        "metrics": metrics,
    }


def render_report(state: PulseState) -> PulseState:
    """Parse report draft and render it to a markdown file on disk."""
    if state.get("skip", False):
        return {}

    logger.info("Node [render_report] starting")
    config = load_config(state["product_slug"])
    report_json = state.get("report_json", {})
    reviews = state.get("scrubbed_reviews", [])

    # Strip failed quotes if validation failed but we proceeded
    valid_quotes_list = []
    failed_texts = {fq["text"] for fq in state.get("quote_validation", {}).get("failed_quotes", [])}

    from rapidfuzz import fuzz

    for q in report_json.get("quotes", []):
        quote_text = q.get("text", "")
        if quote_text not in failed_texts:
            # Fuzzy match to find the actual source review to extract metadata (date, source, rating)
            best_rev = None
            best_score = -1.0
            for r in reviews:
                score = fuzz.token_set_ratio(quote_text, r.text)
                if score > best_score:
                    best_score = score
                    best_rev = r

            quote_date = best_rev.review_date if best_rev else None
            quote_rating = best_rev.rating if best_rev else q.get("rating")
            quote_source = best_rev.source if best_rev else q.get("source")

            valid_quotes_list.append(
                ReportQuote(
                    text=quote_text,
                    rating=quote_rating,
                    source=quote_source,
                    review_date=quote_date,
                    theme_label=q.get("theme_label"),
                )
            )

    draft = ReportDraft(
        summary=report_json.get("summary", ""),
        themes=[
            ReportTheme(
                label=t.get("label"),
                description=t.get("description"),
                review_count=t.get("review_count"),
                avg_rating=t.get("avg_rating"),
            )
            for t in report_json.get("themes", [])
        ],
        quotes=valid_quotes_list,
        action_ideas=report_json.get("action_ideas", []),
    )

    # Render report to markdown file on disk
    from review_pulse.render.markdown import render_markdown_report

    report_content = render_markdown_report(
        draft=draft,
        product_slug=state["product_slug"],
        product_display_name=config.product.display_name,
        week_start=state["week_start"],
        week_end=state["week_end"],
        reviews_count=len(reviews),
        run_id=state["run_id"],
        config=config,
    )

    # Compute actual report path
    from review_pulse.config import PROJECT_ROOT
    report_filename = f"{state['product_slug']}-{state['week_start'].isoformat()}.md"
    report_path = str(PROJECT_ROOT / "data" / "reports" / report_filename)

    return {
        "report_draft": draft,
        "report_path": report_path,
    }


def deliver_report(state: PulseState) -> PulseState:
    """Deliver the report by sending POST requests to the local MCP server."""
    if state.get("skip", False):
        return {}

    if state.get("dry_run", False):
        logger.info("Dry run requested; bypassing Google delivery steps.")
        return {
            "google_doc_id": None,
            "email_sent": False,
        }

    logger.info("Node [deliver_report] starting")
    settings = load_settings()
    config = load_config(state["product_slug"])

    # Query DB to check for existing document ID (for idempotency)
    repo = RunRepository(settings.database_path)
    existing_run = repo.get_run_by_product_week(state["product_slug"], state["week_start"])
    
    # Doc ID check: prioritize config setting, then fallback to db
    doc_id = settings.google_doc_id or (existing_run.google_doc_id if existing_run else None)
    already_emailed = existing_run.email_sent if existing_run else False

    # 1. Google Doc delivery via MCP (if a doc_id is configured/found)
    doc_delivered = False
    if doc_id:
        from pathlib import Path
        report_path = state.get("report_path")
        if report_path and Path(report_path).exists():
            markdown_content = Path(report_path).read_text(encoding="utf-8")
            
            from review_pulse.deliver.mcp_client import deliver_doc_via_mcp
            logger.info(
                "[Delivery] Preparing Google Docs delivery — doc_id=%s content_size=%d bytes",
                doc_id,
                len(markdown_content.encode("utf-8")),
            )
            doc_delivered = deliver_doc_via_mcp(
                doc_id=doc_id,
                content=markdown_content,
            )
            if doc_delivered:
                logger.info("[Delivery] Google Docs delivery succeeded — doc_id=%s", doc_id)
            else:
                logger.error("[Delivery] Google Docs delivery FAILED — doc_id=%s", doc_id)
        else:
            logger.warning("Report file not found at %s; skipping doc append", report_path)
    else:
        logger.warning(
            "No GOOGLE_DOC_ID configured in Settings or runs database. "
            "Skipping Google Docs delivery step."
        )

    # 2. Gmail draft creation via MCP
    email_sent = already_emailed
    if doc_id and doc_delivered and not already_emailed and settings.email_recipients:
        draft = state["report_draft"]
        
        # Format HTML body
        themes_html_list = []
        for i, theme in enumerate(draft.themes, start=1):
            avg_rating_str = f"{theme.avg_rating:.1f}" if theme.avg_rating is not None else "-"
            themes_html_list.append(
                f"<li><strong>{theme.label.strip()}</strong> — {theme.description.strip()} "
                f"({theme.review_count} reviews, avg ★{avg_rating_str})</li>"
            )
        themes_html = "\n".join(themes_html_list)

        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        doc_link_html = f'<p><strong>Google Doc Report:</strong> <a href="{doc_url}">{doc_url}</a></p>'

        html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
    <h2 style="color: #1a73e8; border-bottom: 1px solid #ddd; padding-bottom: 8px;">
        {config.product.display_name} — Weekly Review Pulse (Week {state["iso_week"]})
    </h2>
    
    <h3>Executive Summary</h3>
    <p>{draft.summary.strip()}</p>
    
    <h3>Top Themes This Week</h3>
    <ul>
        {themes_html}
    </ul>
    
    {doc_link_html}
    
    <hr style="border: 0; border-top: 1px solid #ddd; margin-top: 24px;" />
    <p style="font-size: 12px; color: #777; font-style: italic;">
        Generated automatically by Review Pulse.
    </p>
</body>
</html>
"""

        subject = f"{config.product.display_name} Weekly Review Pulse — Week {state['iso_week']}"
        
        from review_pulse.deliver.mcp_client import deliver_email_via_mcp
        logger.info(
            "[Delivery] Preparing Gmail draft delivery — to=%s subject=%s",
            settings.email_recipients,
            subject[:80],
        )
        email_sent = deliver_email_via_mcp(
            to=settings.email_recipients,
            subject=subject,
            body=html_body,
        )
        if email_sent:
            logger.info("[Delivery] Gmail draft delivery succeeded — to=%s", settings.email_recipients)
        else:
            logger.error("[Delivery] Gmail draft delivery FAILED — to=%s", settings.email_recipients)
    elif already_emailed:
        logger.info("Email was already sent for this week; skipping duplicate send.")

    # Return doc_id only if it was successfully delivered
    return {
        "google_doc_id": doc_id if doc_delivered else None,
        "email_sent": email_sent,
    }


def audit_log(state: PulseState) -> PulseState:
    """Record run details, status, and metrics into database."""
    if state.get("skip", False):
        return {}

    logger.info("Node [audit_log] starting")
    settings = load_settings()
    repo = RunRepository(settings.database_path)
    run_id = state["run_id"]

    metrics = state.get("metrics") or RunMetrics()

    # Save reviews to sqlite
    repo.save_reviews(run_id, state.get("scrubbed_reviews", []))
    repo.update_review_cluster_ids(run_id, state.get("scrubbed_reviews", []))
    repo.update_run_reviews_count(
        run_id,
        reviews_fetched=metrics.reviews_fetched,
        reviews_processed=metrics.reviews_processed,
    )

    # Save themes to sqlite
    repo.save_themes(run_id, state.get("themes", []))

    # Update runs status to completed
    repo.update_run_status(
        run_id,
        status="completed",
        completed_at=datetime.now(),
    )

    # Update other database properties
    with repo._connect() as conn:
        conn.execute(
            """
            UPDATE runs
            SET groq_tokens_used = ?,
                report_path = ?,
                google_doc_id = ?,
                email_sent = ?
            WHERE run_id = ?
            """,
            (
                metrics.groq_tokens_used,
                state.get("report_path"),
                state.get("google_doc_id"),
                state.get("email_sent", False),
                run_id,
            ),
        )
        conn.commit()

    logger.info("Pipeline run logged successfully")
    return {}
