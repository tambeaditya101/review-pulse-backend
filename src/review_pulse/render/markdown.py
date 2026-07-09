"""Markdown report rendering following the INDMoney sample template."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

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
) -> str:
    """Generate a formatted markdown report and save it to disk.

    Args:
        draft: The populated ReportDraft object.
        product_slug: Slug of the product (e.g. 'indmoney').
        product_display_name: Display name of the product (e.g. 'INDMoney').
        week_start: Start date of report week.
        week_end: End date of report week.
        reviews_count: Total reviews analyzed.
        run_id: Unique pipeline run identifier.
        config: Loaded product config to enforce max limits.
        output_dir: Optional path override to write report file.

    Returns:
        The generated markdown report string.
    """
    logger.info("Rendering markdown report for run %s", run_id)

    # Format header
    week_start_str = week_start.strftime("%B %d, %Y")
    week_end_str = week_end.strftime("%B %d, %Y")
    lines = [
        f"# {product_display_name} — Weekly Review Pulse",
        "",
        f"**Period:** {week_start_str} – {week_end_str}  ",
        "**Sources:** Google Play, Apple App Store  ",
        f"**Reviews analyzed:** {reviews_count}",
        "",
        "## Executive Summary",
        draft.summary.strip(),
        "",
        "## Top Themes",
    ]

    # Render Themes (limit to max_themes)
    max_themes = config.report.max_themes
    themes_to_render = draft.themes[:max_themes]
    for i, theme in enumerate(themes_to_render, start=1):
        # average rating display formatting
        avg_rating_str = f"{theme.avg_rating:.1f}" if theme.avg_rating is not None else "-"
        lines.append(
            f"{i}. **{theme.label.strip()}** — {theme.description.strip()} "
            f"({theme.review_count} reviews, avg ★{avg_rating_str})"
        )

    lines.append("")
    lines.append("## Representative Quotes")

    # Render Quotes (limit to max_quotes_per_theme per theme, up to total limit)
    # Group quotes by theme label
    from collections import defaultdict
    quotes_by_theme = defaultdict(list)
    for q in draft.quotes:
        if q.theme_label:
            quotes_by_theme[q.theme_label.strip()].append(q)

    # Keep track of quotes printed to respect total maximum constraint
    quotes_printed = 0
    max_quotes_per_theme = config.report.max_quotes_per_theme

    # Iterate over rendered themes to print their representative quotes
    for theme in themes_to_render:
        theme_quotes = quotes_by_theme.get(theme.label.strip(), [])
        # Also check fallback if theme label matches slightly or is listed in another key format
        if not theme_quotes:
            # Case-insensitive / whitespace-insensitive fallback check
            theme_label_normalized = theme.label.strip().lower()
            for label, q_list in quotes_by_theme.items():
                if label.lower() == theme_label_normalized:
                    theme_quotes = q_list
                    break

        # Limit quotes per theme
        theme_quotes_limited = theme_quotes[:max_quotes_per_theme]

        for q in theme_quotes_limited:
            rating_str = f"{q.rating}★" if q.rating is not None else "★-"
            source_str = "Google Play" if q.source == "google_play" else "App Store"
            date_str = q.review_date.strftime("%Y-%m-%d") if q.review_date else "unknown date"

            lines.append(
                f'> "{q.text.strip()}" — {rating_str}, {source_str}, {date_str}'
            )
            quotes_printed += 1

    # Fallback: if no quotes were printed under the theme groups, output up to max_themes * max_quotes_per_theme first quotes
    if quotes_printed == 0 and draft.quotes:
        for q in draft.quotes[:max_themes * max_quotes_per_theme]:
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
