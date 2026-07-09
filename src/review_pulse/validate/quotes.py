"""Quote validation — fuzzy-match LLM quotes against source review text.

Uses ``rapidfuzz.fuzz.token_set_ratio`` to verify each quote is a close
match (≥ threshold) to at least one source review. This catches
hallucinated or paraphrased quotes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from review_pulse.models import Review

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 90  # token_set_ratio score


@dataclass
class QuoteValidationResult:
    """Result of validating all quotes in a report draft."""

    valid: bool = True
    total_quotes: int = 0
    passed_quotes: int = 0
    failed_quotes: list[dict] = field(default_factory=list)


def validate_quote(
    quote_text: str,
    reviews: list[Review],
    threshold: int = _DEFAULT_THRESHOLD,
) -> tuple[bool, float]:
    """Check if a quote closely matches any source review.

    Returns:
        (is_valid, best_score) — True if best score ≥ threshold.
    """
    best_score = 0.0
    for review in reviews:
        score = fuzz.token_set_ratio(quote_text, review.text)
        if score > best_score:
            best_score = score
        if score >= threshold:
            return True, best_score

    return False, best_score


def validate_all_quotes(
    quotes: list[dict],
    reviews: list[Review],
    threshold: int = _DEFAULT_THRESHOLD,
) -> QuoteValidationResult:
    """Validate all quotes from an LLM report against source reviews.

    Args:
        quotes: List of quote dicts with at least a ``text`` key.
        reviews: Source reviews to match against.
        threshold: Minimum ``token_set_ratio`` score to pass.

    Returns:
        ``QuoteValidationResult`` with pass/fail details.
    """
    result = QuoteValidationResult(total_quotes=len(quotes))

    for quote in quotes:
        text = quote.get("text", "")
        is_valid, score = validate_quote(text, reviews, threshold)

        if is_valid:
            result.passed_quotes += 1
            logger.debug("Quote PASSED (score=%.1f): %.50s…", score, text)
        else:
            result.valid = False
            result.failed_quotes.append({
                "text": text,
                "best_score": round(score, 1),
                "theme_label": quote.get("theme_label", ""),
            })
            logger.warning(
                "Quote FAILED (score=%.1f < %d): %.50s…",
                score, threshold, text,
            )

    logger.info(
        "Quote validation: %d/%d passed (threshold=%d)",
        result.passed_quotes,
        result.total_quotes,
        threshold,
    )

    return result
