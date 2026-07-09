from __future__ import annotations

import typer

from review_pulse.config import (
    current_iso_week,
    load_config,
    load_settings,
    parse_iso_week,
    review_window_for_week,
)
from review_pulse.db.repository import RunRepository, init_db

app = typer.Typer(
    name="review-pulse",
    help="Weekly AI-powered app review pulse for INDMoney.",
    no_args_is_help=True,
)


@app.command("init-db")
def init_db_command() -> None:
    """Create the SQLite database and apply schema."""
    settings = load_settings()
    init_db(settings.database_path)
    typer.echo(f"Database initialized at {settings.database_path}")


@app.callback()
def main_callback() -> None:
    """Initialize base human-readable logging for CLI queries."""
    from review_pulse.logging import setup_logging
    setup_logging(json_stdout=False)


@app.command("run")
def run_command(
    product: str = typer.Option(..., "--product", "-p", help="Product slug (e.g. indmoney)"),
    week: str | None = typer.Option(
        None,
        "--week",
        "-w",
        help="ISO week to run for (e.g. 2026-W14). Defaults to current week.",
    ),
    force: bool = typer.Option(False, "--force", help="Re-run even if already completed."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip delivery steps (Phase P5+).",
    ),
) -> None:
    """Run the review pulse pipeline for a product/week."""
    from review_pulse.logging import setup_logging
    setup_logging(json_stdout=True)

    settings = load_settings()
    config = load_config(product)
    repo = RunRepository(settings.database_path)

    iso_week = week or current_iso_week()
    week_start, week_end = parse_iso_week(iso_week)
    window_start, window_end = review_window_for_week(week_start, settings.review_window_weeks)

    existing = repo.get_run_by_product_week(config.product.slug, week_start)
    if existing and existing.status == "completed" and not force:
        typer.echo(
            f"Run already completed for {config.product.display_name} week {iso_week}. "
            "Use --force to re-run."
        )
        raise typer.Exit(code=0)

    typer.echo(f"Product: {config.product.display_name}")
    typer.echo(f"Report week: {iso_week} ({week_start} to {week_end})")
    typer.echo(f"Review window: {window_start} to {window_end} ({settings.review_window_weeks} weeks)")
    if dry_run:
        typer.echo("Dry run: delivery will be skipped.")
    typer.echo("")

    try:
        from datetime import datetime
        from review_pulse.graph.builder import build_pulse_graph

        graph = build_pulse_graph().compile()

        initial_state = {
            "product_slug": config.product.slug,
            "iso_week": iso_week,
            "week_start": week_start,
            "week_end": week_end,
            "window_start": window_start,
            "window_end": window_end,
            "force": force,
            "dry_run": dry_run,
            "skip": False,
        }

        typer.echo("Running Review Pulse pipeline graph...")
        result = graph.invoke(initial_state)

        if result.get("skip", False):
            typer.echo(f"Run skipped due to idempotency for week {iso_week}.")
        else:
            typer.echo("Pipeline completed successfully!")
            run_id = result.get("run_id")
            if run_id:
                metrics = result.get("metrics")
                if metrics:
                    typer.echo(f"  Reviews fetched:   {metrics.reviews_fetched}")
                    typer.echo(f"  Reviews processed: {metrics.reviews_processed}")
                    typer.echo(f"  Themes found:      {metrics.themes_found}")
                    typer.echo(f"  Groq tokens used:  {metrics.groq_tokens_used}")

    except Exception as exc:
        typer.echo(f"Pipeline failed: {exc}", err=True)
        try:
            existing_run = repo.get_run_by_product_week(config.product.slug, week_start)
            if existing_run and existing_run.status == "running":
                repo.update_run_status(
                    existing_run.run_id,
                    status="failed",
                    error_message=str(exc),
                    completed_at=datetime.now(),
                )
        except Exception as db_exc:
            typer.echo(f"Failed to record run failure in DB: {db_exc}", err=True)
        raise typer.Exit(code=1)


@app.command("status")
def status_command(
    product: str = typer.Option(..., "--product", "-p", help="Product slug (e.g. indmoney)"),
    week: str | None = typer.Option(
        None,
        "--week",
        "-w",
        help="Filter to a specific ISO week (e.g. 2026-W14).",
    ),
    limit: int = typer.Option(5, "--limit", "-n", help="Number of recent runs to show."),
) -> None:
    """Show run history and audit status for a product."""
    settings = load_settings()
    config = load_config(product)
    repo = RunRepository(settings.database_path)

    if week:
        week_start, week_end = parse_iso_week(week)
        run = repo.get_run_by_product_week(config.product.slug, week_start)
        if not run:
            typer.echo(f"No run found for {config.product.display_name} week {week}.")
            raise typer.Exit(code=0)

        _print_run(run)
        raise typer.Exit(code=0)

    runs = repo.list_runs(config.product.slug, limit=limit)
    if not runs:
        typer.echo(f"No runs found for {config.product.display_name}.")
        typer.echo("Initialize with: python -m review_pulse init-db")
        raise typer.Exit(code=0)

    typer.echo(f"Recent runs for {config.product.display_name}:")
    typer.echo("")
    for run in runs:
        _print_run(run)
        typer.echo("")


def _print_run(run: object) -> None:
    from review_pulse.models import RunRecord

    assert isinstance(run, RunRecord)
    typer.echo(f"  Run ID:           {run.run_id}")
    typer.echo(f"  Week:             {run.week_start} to {run.week_end}")
    typer.echo(f"  Status:           {run.status}")
    typer.echo(f"  Reviews fetched:  {run.reviews_fetched}")
    typer.echo(f"  Reviews processed:{run.reviews_processed}")
    typer.echo(f"  Report path:      {run.report_path or '-'}")
    typer.echo(f"  Google Doc ID:    {run.google_doc_id or '-'}")
    typer.echo(f"  Email sent:       {run.email_sent}")
    if run.error_message:
        typer.echo(f"  Error:            {run.error_message}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
