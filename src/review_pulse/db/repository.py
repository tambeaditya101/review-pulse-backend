from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from review_pulse.config import PROJECT_ROOT
from review_pulse.models import Review, RunRecord, RunStatus

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_db(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(database_path) as conn:
        conn.executescript(schema_sql)
        conn.commit()


class RunRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        if not database_path.exists():
            init_db(database_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            product=row["product"],
            week_start=date.fromisoformat(row["week_start"]),
            week_end=date.fromisoformat(row["week_end"]),
            status=row["status"],
            reviews_fetched=row["reviews_fetched"],
            reviews_processed=row["reviews_processed"],
            report_path=row["report_path"],
            google_doc_id=row["google_doc_id"],
            email_sent=bool(row["email_sent"]),
            groq_tokens_used=row["groq_tokens_used"],
            error_message=row["error_message"],
            started_at=_parse_datetime(row["started_at"]),
            completed_at=_parse_datetime(row["completed_at"]),
        )

    def create_run(
        self,
        product: str,
        week_start: date,
        week_end: date,
        status: RunStatus = "pending",
    ) -> RunRecord:
        run = RunRecord(
            run_id=str(uuid4()),
            product=product,
            week_start=week_start,
            week_end=week_end,
            status=status,
            started_at=datetime.now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, product, week_start, week_end, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.product,
                    run.week_start.isoformat(),
                    run.week_end.isoformat(),
                    run.status,
                    run.started_at.isoformat(),
                ),
            )
            conn.commit()
        return run

    def get_run_by_product_week(self, product: str, week_start: date) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM runs
                WHERE product = ? AND week_start = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (product, week_start.isoformat()),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def get_run_by_id(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, product: str, limit: int = 5) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE product = ?
                ORDER BY week_start DESC, started_at DESC
                LIMIT ?
                """,
                (product, limit),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    error_message = ?,
                    completed_at = COALESCE(?, completed_at)
                WHERE run_id = ?
                """,
                (
                    status,
                    error_message,
                    completed_at.isoformat() if completed_at else None,
                    run_id,
                ),
            )
            conn.commit()

    def save_reviews(self, run_id: str, reviews: list[Review]) -> int:
        """Persist cleaned reviews to the reviews table.

        Uses INSERT OR REPLACE so re-runs with --force safely overwrite.

        Returns:
            Number of reviews saved.
        """
        if not reviews:
            return 0

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO reviews (
                    review_id, source, run_id, text_clean, rating, review_date
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.review_id,
                        r.source,
                        run_id,
                        r.text,
                        r.rating,
                        r.review_date.isoformat(),
                    )
                    for r in reviews
                ],
            )
            conn.commit()
        return len(reviews)

    def get_reviews_for_run(self, run_id: str) -> list[Review]:
        """Retrieve all reviews associated with a run."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT review_id, source, text_clean, rating, review_date
                FROM reviews
                WHERE run_id = ?
                ORDER BY review_date DESC
                """,
                (run_id,),
            ).fetchall()

        return [
            Review(
                review_id=row["review_id"],
                source=row["source"],
                text=row["text_clean"],
                rating=row["rating"],
                review_date=date.fromisoformat(row["review_date"]),
            )
            for row in rows
        ]

    def update_run_reviews_count(
        self,
        run_id: str,
        *,
        reviews_fetched: int | None = None,
        reviews_processed: int | None = None,
    ) -> None:
        """Update review count fields on a run record."""
        with self._connect() as conn:
            if reviews_fetched is not None:
                conn.execute(
                    "UPDATE runs SET reviews_fetched = ? WHERE run_id = ?",
                    (reviews_fetched, run_id),
                )
            if reviews_processed is not None:
                conn.execute(
                    "UPDATE runs SET reviews_processed = ? WHERE run_id = ?",
                    (reviews_processed, run_id),
                )
            conn.commit()

    def save_themes(self, run_id: str, themes: list) -> int:
        """Persist ThemeCluster objects to the themes table.

        Args:
            run_id: Run to associate themes with.
            themes: List of ThemeCluster objects.

        Returns:
            Number of themes saved.
        """
        if not themes:
            return 0

        with self._connect() as conn:
            # Clear previous themes for this run (idempotent on retry)
            conn.execute("DELETE FROM themes WHERE run_id = ?", (run_id,))
            conn.executemany(
                """
                INSERT INTO themes (run_id, label, description, review_count, avg_rating)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (run_id, t.label, t.description, t.review_count, t.avg_rating)
                    for t in themes
                ],
            )
            conn.commit()
        return len(themes)

    def update_review_cluster_ids(self, run_id: str, reviews: list) -> None:
        """Update cluster_id on reviews that have been tagged by clustering.

        Expects each review to have a ``cluster_id`` attribute set
        (added dynamically by ``cluster_reviews``).
        """
        with self._connect() as conn:
            for r in reviews:
                cluster_id = getattr(r, "cluster_id", None)
                if cluster_id is not None:
                    conn.execute(
                        """
                        UPDATE reviews SET cluster_id = ?
                        WHERE review_id = ? AND source = ? AND run_id = ?
                        """,
                        (cluster_id, r.review_id, r.source, run_id),
                    )
            conn.commit()

    def get_themes_for_run(self, run_id: str) -> list[dict]:
        """Retrieve all themes for a run as dicts."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT label, description, review_count, avg_rating
                FROM themes
                WHERE run_id = ?
                ORDER BY review_count DESC
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
