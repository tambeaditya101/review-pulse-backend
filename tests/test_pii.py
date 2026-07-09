"""Tests for review_pulse.process.pii — PII scrubbing patterns."""

from __future__ import annotations

import pytest

from review_pulse.process.pii import scrub_pii, scrub_reviews
from review_pulse.models import Review
from datetime import date


# ──────────────────────────────────────────────────────────────
#  Email
# ──────────────────────────────────────────────────────────────


class TestEmail:
    def test_simple_email(self) -> None:
        assert "[REDACTED]" in scrub_pii("Contact me at test@example.com")

    def test_email_in_sentence(self) -> None:
        result = scrub_pii("Please reach out to user.name+tag@domain.co.in for help")
        assert "@" not in result
        assert "[REDACTED]" in result

    def test_multiple_emails(self) -> None:
        text = "Email me at a@b.com or c@d.org"
        result = scrub_pii(text)
        assert result.count("[REDACTED]") == 2

    def test_no_email(self) -> None:
        text = "No contact info here"
        assert scrub_pii(text) == text


# ──────────────────────────────────────────────────────────────
#  Indian phone numbers
# ──────────────────────────────────────────────────────────────


class TestPhoneIN:
    def test_plain_10_digit(self) -> None:
        assert "[REDACTED]" in scrub_pii("Call 9876543210 for details")

    def test_with_plus91(self) -> None:
        assert "[REDACTED]" in scrub_pii("Reach me at +919876543210")

    def test_with_plus91_dash(self) -> None:
        assert "[REDACTED]" in scrub_pii("Phone: +91-9876543210")

    def test_with_zero_prefix(self) -> None:
        assert "[REDACTED]" in scrub_pii("Office: 09876543210")

    def test_does_not_match_non_mobile_start(self) -> None:
        # Indian mobile numbers start with 6-9
        text = "Reference: 1234567890"
        # This should still be caught by long_digits if >= 8 digits, but
        # the phone pattern specifically shouldn't match a leading 1
        result = scrub_pii(text)
        assert "[REDACTED]" in result  # caught by long_digits


# ──────────────────────────────────────────────────────────────
#  PAN card
# ──────────────────────────────────────────────────────────────


class TestPAN:
    def test_valid_pan(self) -> None:
        assert "[REDACTED]" in scrub_pii("My PAN is ABCDE1234F")

    def test_pan_in_sentence(self) -> None:
        result = scrub_pii("PAN number ZZZZZ9999Z was rejected")
        assert "ZZZZZ9999Z" not in result
        assert "[REDACTED]" in result

    def test_lowercase_pan_not_matched(self) -> None:
        # PAN is uppercase only
        text = "abcde1234f is not a real PAN"
        result = scrub_pii(text)
        assert "abcde1234f" in result  # Not redacted (lowercase)


# ──────────────────────────────────────────────────────────────
#  Aadhaar-like numbers
# ──────────────────────────────────────────────────────────────


class TestAadhaar:
    def test_continuous_12_digits(self) -> None:
        assert "[REDACTED]" in scrub_pii("Aadhaar: 123456789012")

    def test_space_separated(self) -> None:
        assert "[REDACTED]" in scrub_pii("Aadhaar: 1234 5678 9012")

    def test_dash_separated(self) -> None:
        assert "[REDACTED]" in scrub_pii("ID: 1234-5678-9012")


# ──────────────────────────────────────────────────────────────
#  Long digit sequences
# ──────────────────────────────────────────────────────────────


class TestLongDigits:
    def test_8_digit_account(self) -> None:
        assert "[REDACTED]" in scrub_pii("Account 12345678 is blocked")

    def test_16_digit_card(self) -> None:
        assert "[REDACTED]" in scrub_pii("Card: 4111111111111111")

    def test_short_digits_kept(self) -> None:
        # 4-digit PIN should NOT be redacted
        text = "Enter PIN 1234"
        assert "1234" in scrub_pii(text)


# ──────────────────────────────────────────────────────────────
#  Combined / integration
# ──────────────────────────────────────────────────────────────


class TestCombined:
    def test_multiple_pii_types(self) -> None:
        text = "Email me at user@test.com, PAN is ABCDE1234F, phone +919876543210"
        result = scrub_pii(text)
        assert "user@test.com" not in result
        assert "ABCDE1234F" not in result
        assert "9876543210" not in result
        assert result.count("[REDACTED]") >= 3

    def test_no_pii_unchanged(self) -> None:
        text = "Great app! Love the mutual fund features. 5 stars."
        assert scrub_pii(text) == text

    def test_preserves_surrounding_text(self) -> None:
        result = scrub_pii("Call 9876543210 now!")
        assert result.startswith("Call ")
        assert result.endswith(" now!")


# ──────────────────────────────────────────────────────────────
#  scrub_reviews helper
# ──────────────────────────────────────────────────────────────


class TestScrubReviews:
    def test_scrubs_review_objects(self) -> None:
        r = Review(
            review_id="test",
            source="google_play",
            text="Email me at pii@test.com please",
            rating=5,
            review_date=date(2026, 6, 15),
        )
        scrub_reviews([r])
        assert "pii@test.com" not in r.text
        assert "[REDACTED]" in r.text

    def test_scrubs_dicts(self) -> None:
        d = {"text": "PAN: ABCDE1234F"}
        scrub_reviews([d])
        assert "ABCDE1234F" not in d["text"]
        assert "[REDACTED]" in d["text"]
