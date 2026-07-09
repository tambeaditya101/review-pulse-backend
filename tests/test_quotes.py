"""Unit tests for quote validation."""

from __future__ import annotations

from datetime import date
import pytest

from review_pulse.models import Review
from review_pulse.validate.quotes import validate_quote, validate_all_quotes


def _make_review(text: str) -> Review:
    return Review(
        review_id="r1",
        source="google_play",
        text=text,
        rating=5,
        review_date=date(2026, 6, 15),
    )


def test_validate_quote_exact_match() -> None:
    reviews = [_make_review("This is a fantastic investment app with zero brokerage")]
    # Exact match should easily pass
    is_valid, score = validate_quote("fantastic investment app", reviews)
    assert is_valid
    assert score >= 90


def test_validate_quote_fuzzy_match() -> None:
    reviews = [_make_review("This is a fantastic investment app with zero brokerage")]
    # Slight variation / substring should pass with default threshold
    is_valid, score = validate_quote("fantastic investment app zero brokerage", reviews)
    assert is_valid
    assert score >= 90


def test_validate_quote_hallucinated() -> None:
    reviews = [_make_review("This is a fantastic investment app with zero brokerage")]
    # Completely hallucinated quote should fail
    is_valid, score = validate_quote("totally bad support and high fees", reviews)
    assert not is_valid
    assert score < 90


def test_validate_all_quotes() -> None:
    reviews = [
        _make_review("I love the UI and stock search feature."),
        _make_review("Redemption is very slow, took me 3 days."),
    ]
    quotes = [
        {"text": "love the UI and stock search", "theme_label": "UI"},
        {"text": "Redemption is very slow, took me 3 days", "theme_label": "Performance"},
        {"text": "This app is completely garbage and crashes", "theme_label": "Stability"},
    ]

    result = validate_all_quotes(quotes, reviews)
    assert not result.valid
    assert result.total_quotes == 3
    assert result.passed_quotes == 2
    assert len(result.failed_quotes) == 1
    assert result.failed_quotes[0]["text"] == "This app is completely garbage and crashes"
