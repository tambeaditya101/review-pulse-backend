"""Groq LLM client — wraps ChatGroq with fallback model and retry logic."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from review_pulse.llm.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_PRIMARY_MODEL = "llama-3.3-70b-versatile"
_FALLBACK_MODEL = "llama-3.1-8b-instant"
_MAX_RETRIES = 3
_BASE_BACKOFF = 2.0  # seconds


def generate_report_json(
    api_key: str,
    user_prompt: str,
    model: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Call Groq to generate a structured JSON report.

    Tries the primary model first, falls back to the smaller model on
    HTTP 429 or persistent errors.

    Args:
        api_key: Groq API key.
        user_prompt: Fully formatted user prompt with review data.
        model: Override model name (for testing).

    Returns:
        Tuple of (parsed JSON dict, total tokens used).
    """
    from groq import Groq

    client = Groq(api_key=api_key)
    models_to_try = [model or _PRIMARY_MODEL, _FALLBACK_MODEL]
    last_error: Exception | None = None
    total_tokens = 0

    for current_model in models_to_try:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                logger.info(
                    "Groq call: model=%s attempt=%d/%d",
                    current_model, attempt, _MAX_RETRIES,
                )

                response = client.chat.completions.create(
                    model=current_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=4096,
                    response_format={"type": "json_object"},
                )

                # Track token usage
                if response.usage:
                    total_tokens += response.usage.total_tokens
                    logger.info(
                        "Groq tokens: prompt=%d completion=%d total=%d",
                        response.usage.prompt_tokens,
                        response.usage.completion_tokens,
                        response.usage.total_tokens,
                    )

                # Parse response
                content = response.choices[0].message.content
                if not content:
                    raise ValueError("Empty response from Groq")

                parsed = json.loads(content)
                logger.info("Groq response parsed successfully")
                return parsed, total_tokens

            except json.JSONDecodeError as e:
                logger.warning(
                    "JSON parse error from Groq (attempt %d): %s",
                    attempt, e,
                )
                last_error = e
                # Retry with same model
                time.sleep(_BASE_BACKOFF * attempt)

            except Exception as e:
                error_str = str(e).lower()
                logger.warning(
                    "Groq error (model=%s attempt=%d): %s",
                    current_model, attempt, e,
                )
                last_error = e

                # Rate limit — backoff
                if "429" in error_str or "rate" in error_str:
                    wait = _BASE_BACKOFF * (2 ** attempt)
                    logger.info("Rate limited; waiting %.1fs", wait)
                    time.sleep(wait)
                    continue

                # Other errors — backoff and maybe try fallback model
                time.sleep(_BASE_BACKOFF * attempt)

        logger.warning("Exhausted retries for model %s", current_model)

    raise RuntimeError(
        f"Groq generation failed after trying all models. Last error: {last_error}"
    )
