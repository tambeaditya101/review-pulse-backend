"""Markdown report rendering following the INDMoney sample template."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from review_pulse.config import AppConfig, PROJECT_ROOT
from review_pulse.models import ReportDraft

logger = logging.getLogger(__name__)

# Output directory for markdown reports
_REPORT_DIR = PROJECT_ROOT / "data" / "reports"


def render_markdown_report(
    draft: ReportDraft,
    product_slug: str,
    product_display_name: str,
    week_start: date,
    week_end: date,
    reviews_count: int,
    run_id: str,
    config: AppConfig,
    output_dir: Path | None = None,
    # Fix 2: analysis window for the raw markdown header
    window_start: date | None = None,
    window_end: date | None = None,
    # Fix 3: cluster_id → ThemeCluster lookup for reliable quote grouping
    cluster_lookup: dict[int, Any] | None = None,
) -> str:
    """Generate a formatted markdown report and save it to disk.

    Args:
        draft: The populated ReportDraft object.
        product_slug: Slug of the product (e.g. 'indmoney').
        product_display_name: Display name of the product (e.g. 'INDMoney').
        week_start: Start date of report week.
        week_end: End date of report week.
        reviews_count: Total reviews analyzed (from analysis window).
        run_id: Unique pipeline run identifier.
        config: Loaded product config to enforce max limits.
        output_dir: Optional path override to write report file.
        window_start: Analysis window start date (10-week lookback).
        window_end: Analysis window end date (same as week_end).
        cluster_lookup: Maps cluster_id (int) → ThemeCluster; used to group
            quotes by stable cluster_id rather than fragile string label.

    Returns:
        The generated markdown report string.
    """
    logger.info("Rendering markdown report for run %s", run_id)

    # --- Fix 2: Header clearly distinguishes Reporting Week from Analysis Window ---
    week_start_str = week_start.strftime("%B %d, %Y")
    week_end_str = week_end.strftime("%B %d, %Y")

    lines = [
        f"# {product_display_name} — Weekly Review Pulse",
        "",
        f"**Reporting Week:** {week_start_str} – {week_end_str}",
    ]

    if window_start and window_end:
        win_start_str = window_start.strftime("%B %d, %Y")
        win_end_str = window_end.strftime("%B %d, %Y")
        lines.append(f"**Analysis Window:** {win_start_str} – {win_end_str} (10 weeks)")

    lines += [
        "**Sources:** Google Play, Apple App Store",
        f"**Reviews Analyzed:** {reviews_count}",
        "",
        "## Executive Summary",
        draft.summary.strip(),
        "",
        "## Top Themes",
    ]

    # --- Render Themes (limit to max_themes) ---
    # Fix 1: review_count and avg_rating come from authoritative ThemeCluster objects
    # via draft.themes (which nodes.py now populates from state["themes"], not LLM JSON).
    max_themes = config.report.max_themes
    themes_to_render = draft.themes[:max_themes]
    for i, theme in enumerate(themes_to_render, start=1):
        avg_rating_str = f"{theme.avg_rating:.1f}" if theme.avg_rating is not None else "-"
        lines.append(
            f"{i}. **{theme.label.strip()}** — {theme.description.strip()} "
            f"({theme.review_count} reviews, avg ★{avg_rating_str})"
        )

    lines.append("")
    lines.append("## Representative Quotes")
    lines.append(
        "_Quotes are drawn from the full analysis window and represent "
        "the most illustrative feedback for each theme._"
    )
    lines.append("")

    # --- Fix 3: Group quotes by cluster_id (stable integer) rather than theme_label ---
    # Fix 4: Show at most max_quotes_per_theme quotes per theme (default 1)
    max_quotes_per_theme = config.report.max_quotes_per_theme

    # Build cluster_id → [ReportQuote] mapping
    from collections import defaultdict
    quotes_by_cid: dict[int | None, list] = defaultdict(list)
    for q in draft.quotes:
        cid = getattr(q, "cluster_id", None)
        quotes_by_cid[cid].append(q)

    # Walk themes in render order; look up quotes by cluster_id
    quotes_printed = 0
    for theme in themes_to_render:
        # Resolve the cluster_id for this theme from cluster_lookup
        theme_cid: int | None = None
        if cluster_lookup:
            for cid, tc in cluster_lookup.items():
                if tc.label == theme.label:
                    theme_cid = cid
                    break

        theme_quotes = quotes_by_cid.get(theme_cid, [])

        # Fix 4: limit to max_quotes_per_theme per theme
        for q in theme_quotes[:max_quotes_per_theme]:
            rating_str = f"{q.rating}★" if q.rating is not None else "★-"
            source_str = "Google Play" if q.source == "google_play" else "App Store"
            date_str = q.review_date.strftime("%Y-%m-%d") if q.review_date else "unknown date"
            lines.append(
                f'> "{q.text.strip()}" — {rating_str}, {source_str}, {date_str}'
            )
            lines.append(f'> _{theme.label.strip()} · from analysis window_')
            lines.append("")
            quotes_printed += 1

    # Fallback: if cluster_id resolution yielded nothing, fall back to theme_label matching
    if quotes_printed == 0 and draft.quotes:
        logger.warning(
            "cluster_id-based quote grouping produced no output; "
            "falling back to theme_label string matching"
        )
        from collections import defaultdict as _dd
        quotes_by_label: dict[str, list] = _dd(list)
        for q in draft.quotes:
            if q.theme_label:
                quotes_by_label[q.theme_label.strip().lower()].append(q)

        for theme in themes_to_render:
            label_key = theme.label.strip().lower()
            for q in quotes_by_label.get(label_key, [])[:max_quotes_per_theme]:
                rating_str = f"{q.rating}★" if q.rating is not None else "★-"
                source_str = "Google Play" if q.source == "google_play" else "App Store"
                date_str = q.review_date.strftime("%Y-%m-%d") if q.review_date else "unknown date"
                lines.append(
                    f'> "{q.text.strip()}" — {rating_str}, {source_str}, {date_str}'
                )

    lines.append("")
    lines.append("## Action Ideas")

    # Render Action Ideas
    max_action_ideas = config.report.max_action_ideas
    for idea in draft.action_ideas[:max_action_ideas]:
        lines.append(f"- {idea.strip()}")

    # Render Footer
    lines.append("")
    lines.append("---")
    lines.append(f"_Generated by Review Pulse · Run ID: {run_id}_")
    lines.append("")

    markdown_content = "\n".join(lines)

    # Write report file to disk
    actual_dir = output_dir or _REPORT_DIR
    actual_dir.mkdir(parents=True, exist_ok=True)
    report_filename = f"{product_slug}-{week_start.isoformat()}.md"
    report_path = actual_dir / report_filename

    report_path.write_text(markdown_content, encoding="utf-8")
    logger.info("Saved report markdown file to %s", report_path)

    return markdown_content

