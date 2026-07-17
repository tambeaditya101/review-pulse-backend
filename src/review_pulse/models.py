from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

ReviewSource = Literal["google_play", "app_store"]
RunStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class Review:
    review_id: str
    source: ReviewSource
    text: str
    rating: int
    review_date: date
    title: str | None = None
    author: str | None = None
    app_version: str | None = None
    fetched_at: datetime | None = None


@dataclass
class ThemeCluster:
    cluster_id: int
    label: str
    description: str
    review_count: int
    avg_rating: float
    sample_review_ids: list[str] = field(default_factory=list)


@dataclass
class ReportQuote:
    text: str
    rating: int | None = None
    source: ReviewSource | None = None
    review_date: date | None = None
    theme_label: str | None = None
    cluster_id: int | None = None  # stable backend ID; preferred over theme_label for grouping


@dataclass
class ReportTheme:
    label: str
    description: str
    review_count: int
    avg_rating: float


@dataclass
class ReportDraft:
    summary: str = ""
    themes: list[ReportTheme] = field(default_factory=list)
    quotes: list[ReportQuote] = field(default_factory=list)
    action_ideas: list[str] = field(default_factory=list)


@dataclass
class DeliveryResult:
    google_doc_id: str | None = None
    email_sent: bool = False
    success: bool = False
    error_message: str | None = None


@dataclass
class RunMetrics:
    reviews_fetched: int = 0
    reviews_processed: int = 0
    themes_found: int = 0
    quotes_validated: int = 0
    quotes_dropped: int = 0
    groq_tokens_used: int = 0
    duration_seconds: float = 0.0


@dataclass
class RunRecord:
    run_id: str
    product: str
    week_start: date
    week_end: date
    status: RunStatus
    reviews_fetched: int = 0
    reviews_processed: int = 0
    report_path: str | None = None
    google_doc_id: str | None = None
    email_sent: bool = False
    groq_tokens_used: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
