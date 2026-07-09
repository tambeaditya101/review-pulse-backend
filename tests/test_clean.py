"""Tests for review_pulse.process.clean — text cleaning and deduplication."""

from __future__ import annotations

from datetime import date

import pytest

from review_pulse.models import Review
from review_pulse.process.clean import clean_and_deduplicate, clean_text, dedup_key


# ──────────────────────────────────────────────────────────────
#  clean_text
# ──────────────────────────────────────────────────────────────


class TestCleanText:
    def test_html_entities(self) -> None:
        assert clean_text("Best app &amp; service") == "Best app & service"
        assert clean_text("5 &gt; 3 &lt; 10") == "5 > 3 < 10"
        assert clean_text("&#39;quoted&#39;") == "'quoted'"

    def test_zero_width_chars(self) -> None:
        # \u200b = zero-width space, \u200d = zero-width joiner
        raw = "Hello\u200b\u200dWorld"
        assert clean_text(raw) == "HelloWorld"

    def test_collapse_whitespace(self) -> None:
        raw = "Too   many    spaces\n\nand\tnewlines"
        assert clean_text(raw) == "Too many spaces and newlines"

    def test_strip_outer_whitespace(self) -> None:
        assert clean_text("  padded text  ") == "padded text"

    def test_combined_cleaning(self) -> None:
        raw = "  Great app &amp; UI\u200b   works   well  "
        assert clean_text(raw) == "Great app & UI works well"

    def test_empty_string(self) -> None:
        assert clean_text("") == ""

    def test_only_whitespace(self) -> None:
        assert clean_text("   \n\t  ") == ""


# ──────────────────────────────────────────────────────────────
#  dedup_key
# ──────────────────────────────────────────────────────────────


class TestDedupKey:
    def test_deterministic(self) -> None:
        d = date(2026, 6, 15)
        k1 = dedup_key("Great app", 5, d)
        k2 = dedup_key("Great app", 5, d)
        assert k1 == k2

    def test_case_insensitive(self) -> None:
        d = date(2026, 6, 15)
        assert dedup_key("Great App", 5, d) == dedup_key("great app", 5, d)

    def test_different_ratings_differ(self) -> None:
        d = date(2026, 6, 15)
        assert dedup_key("text", 5, d) != dedup_key("text", 4, d)

    def test_different_dates_differ(self) -> None:
        assert dedup_key("text", 5, date(2026, 6, 15)) != dedup_key(
            "text", 5, date(2026, 6, 16)
        )


# ──────────────────────────────────────────────────────────────
#  clean_and_deduplicate
# ──────────────────────────────────────────────────────────────


def _make_review(
    text: str,
    rating: int = 4,
    review_date: date | None = None,
    source: str = "google_play",
) -> Review:
    d = review_date or date(2026, 6, 15)
    return Review(
        review_id="test",
        source=source,  # type: ignore[arg-type]
        text=text,
        rating=rating,
        review_date=d,
    )


WINDOW_START = date(2026, 6, 1)
WINDOW_END = date(2026, 6, 30)


class TestCleanAndDeduplicate:
    def test_basic_pass_through(self) -> None:
        reviews = [_make_review("This is a valid review text")]
        result = clean_and_deduplicate(reviews, WINDOW_START, WINDOW_END)
        assert len(result) == 1
        assert result[0].text == "This is a valid review text"

    def test_drops_short_reviews(self) -> None:
        reviews = [_make_review("ok"), _make_review("fine")]
        result = clean_and_deduplicate(reviews, WINDOW_START, WINDOW_END)
        assert len(result) == 0

    def test_drops_out_of_window(self) -> None:
        reviews = [
            _make_review("Valid review in window", review_date=date(2026, 6, 15)),
            _make_review("Too old review", review_date=date(2025, 1, 1)),
            _make_review("Future review", review_date=date(2027, 1, 1)),
        ]
        result = clean_and_deduplicate(reviews, WINDOW_START, WINDOW_END)
        assert len(result) == 1

    def test_deduplication(self) -> None:
        """Identical text + rating + date → only one kept."""
        r1 = _make_review("Duplicate review text here", rating=4, review_date=date(2026, 6, 10))
        r2 = _make_review("Duplicate review text here", rating=4, review_date=date(2026, 6, 10))
        result = clean_and_deduplicate([r1, r2], WINDOW_START, WINDOW_END)
        assert len(result) == 1

    def test_cleans_html_entities(self) -> None:
        reviews = [_make_review("Good app &amp; service for investments")]
        result = clean_and_deduplicate(reviews, WINDOW_START, WINDOW_END)
        assert result[0].text == "Good app & service for investments"

    def test_preserves_original_metadata(self) -> None:
        r = Review(
            review_id="meta_test",
            source="app_store",
            text="This review has metadata attached",
            rating=5,
            review_date=date(2026, 6, 15),
            title="A Title",
            author="AuthorName",
        )
        result = clean_and_deduplicate([r], WINDOW_START, WINDOW_END)
        assert len(result) == 1
        assert result[0].source == "app_store"
        assert result[0].title == "A Title"
        assert result[0].author == "AuthorName"

    def test_does_not_mutate_original(self) -> None:
        original_text = "Hello &amp; World  "
        r = _make_review(original_text)
        clean_and_deduplicate([r], WINDOW_START, WINDOW_END)
        # Original review should be unchanged
        assert r.text == original_text

    def test_boundary_dates_included(self) -> None:
        r_start = _make_review("Review on window start", review_date=WINDOW_START)
        r_end = _make_review("Review on window end", review_date=WINDOW_END)
        result = clean_and_deduplicate([r_start, r_end], WINDOW_START, WINDOW_END)
        assert len(result) == 2

    def test_empty_list(self) -> None:
        result = clean_and_deduplicate([], WINDOW_START, WINDOW_END)
        assert result == []
