from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


class ProductConfig(BaseModel):
    slug: str
    display_name: str
    google_play_package: str
    app_store_id: int
    country: str = "in"
    language: str = "en"


class ReportConfig(BaseModel):
    max_themes: int = 5
    max_quotes_per_theme: int = 1  # one representative quote per theme keeps the report concise
    max_action_ideas: int = 5
    max_reviews_per_cluster: int = 4
    max_review_chars: int = 180


class DeliveryConfig(BaseModel):
    google_doc_folder: str = ""
    email_subject_template: str = "INDMoney Weekly Review Pulse — Week {week}"


class AppConfig(BaseModel):
    product: ProductConfig
    report: ReportConfig = Field(default_factory=ReportConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_max_completion_tokens: int = 1500
    google_credentials_path: Path = Path("credentials.json")
    google_token_path: Path = Path("token.json")
    email_recipients: str = ""
    review_window_weeks: int = 10
    database_path: Path = Path("data/review_pulse.db")
    google_doc_id: str = ""
    mcp_server_url: str = "http://127.0.0.1:8000"
    # MCP server authentication and tuning
    mcp_api_key: str | None = None  # X-API-Key header sent to the MCP server
    mcp_timeout_seconds: float = 60.0  # Per-request timeout in seconds
    mcp_max_retries: int = 3  # Number of retry attempts on transient failures

    @field_validator("google_credentials_path", "google_token_path", "database_path", mode="before")
    @classmethod
    def resolve_path(cls, value: Any) -> Path:
        path = Path(value)
        if not path.is_absolute():
            return PROJECT_ROOT / path
        return path

    @property
    def email_recipient_list(self) -> list[str]:
        return [email.strip() for email in self.email_recipients.split(",") if email.strip()]

    def require_groq_api_key(self) -> str:
        if not self.groq_api_key:
            raise ValueError(
                "GROQ_API_KEY is not set. Add it to .env (see .env.example). "
                "Required from Phase P3 onward."
            )
        return self.groq_api_key


def load_settings() -> Settings:
    return Settings()


def load_config(product_slug: str) -> AppConfig:
    config_path = CONFIG_DIR / f"{product_slug}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Product config not found: {config_path}. "
            f"Expected config/{product_slug}.yaml"
        )

    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not raw:
        raise ValueError(f"Product config is empty: {config_path}")

    return AppConfig.model_validate(raw)


def parse_iso_week(iso_week: str) -> tuple[date, date]:
    """Return (monday, sunday) for an ISO week string like 2026-W14."""
    try:
        year_str, week_str = iso_week.upper().split("-W")
        year = int(year_str)
        week = int(week_str)
        if week < 1 or week > 53:
            raise ValueError
        monday = date.fromisocalendar(year, week, 1)
        sunday = date.fromisocalendar(year, week, 7)
        return monday, sunday
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"Invalid ISO week '{iso_week}'. Expected format: YYYY-Www (e.g. 2026-W14)."
        ) from exc


def current_iso_week() -> str:
    today = date.today()
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def review_window_for_week(week_start: date, window_weeks: int) -> tuple[date, date]:
    """Review data window ending on the ISO week's Sunday."""
    week_end = week_start + timedelta(days=6)
    window_start = week_end - timedelta(weeks=window_weeks) + timedelta(days=1)
    return window_start, week_end
