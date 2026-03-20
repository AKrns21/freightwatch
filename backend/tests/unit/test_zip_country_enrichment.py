"""Unit tests for ZIP/country consistency validation — issue #54.

Tests: infer_country_from_zip() + ExtractionValidatorService.validate_zip_countries()
"""

from __future__ import annotations

import pytest

from app.services.extraction_validator_service import (
    ExtractionValidatorService,
    ShipmentCountryInput,
    infer_country_from_zip,
)


# ===========================================================================
# infer_country_from_zip — pure function
# ===========================================================================


class TestInferCountryFromZip:
    # ── DE (5 digits) ────────────────────────────────────────────────────────

    def test_five_digit_zip_returns_de(self) -> None:
        assert infer_country_from_zip("10115") == "DE"

    def test_five_digit_zip_with_leading_zero(self) -> None:
        assert infer_country_from_zip("01069") == "DE"

    def test_five_digit_zip_max(self) -> None:
        assert infer_country_from_zip("99999") == "DE"

    def test_five_digit_zip_strips_whitespace(self) -> None:
        assert infer_country_from_zip("  80331  ") == "DE"

    # ── Ambiguous 4-digit ────────────────────────────────────────────────────

    def test_four_digit_zip_returns_none(self) -> None:
        # Could be AT, CH, BE, LU — cannot determine
        assert infer_country_from_zip("1010") is None  # Vienna AT

    def test_four_digit_zip_ch(self) -> None:
        assert infer_country_from_zip("3000") is None  # Bern CH

    def test_four_digit_zip_be(self) -> None:
        assert infer_country_from_zip("1000") is None  # Brussels BE

    # ── Unknown formats ──────────────────────────────────────────────────────

    def test_none_input_returns_none(self) -> None:
        assert infer_country_from_zip(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert infer_country_from_zip("") is None

    def test_alphanumeric_zip_returns_none(self) -> None:
        assert infer_country_from_zip("SW1A 1AA") is None  # UK

    def test_six_digit_zip_returns_none(self) -> None:
        assert infer_country_from_zip("123456") is None


# ===========================================================================
# validate_zip_countries — validator method
# ===========================================================================


class TestValidateZipCountries:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    # ── Consistent cases — no violations ─────────────────────────────────────

    def test_five_digit_zip_with_de_country_passes(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="10115", dest_country="DE")]
        result = self.svc.validate_zip_countries(inputs)
        assert result.status == "pass"
        assert result.violations == []

    def test_four_digit_zip_with_at_country_passes(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="1010", dest_country="AT")]
        result = self.svc.validate_zip_countries(inputs)
        assert result.status == "pass"
        assert result.violations == []

    def test_four_digit_zip_with_ch_country_passes(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="3000", dest_country="CH")]
        result = self.svc.validate_zip_countries(inputs)
        assert result.status == "pass"
        assert result.violations == []

    def test_four_digit_zip_with_be_country_passes(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="1000", dest_country="BE")]
        result = self.svc.validate_zip_countries(inputs)
        assert result.status == "pass"
        assert result.violations == []

    def test_missing_zip_is_skipped(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip=None, dest_country="DE")]
        result = self.svc.validate_zip_countries(inputs)
        assert result.status == "pass"

    # ── Mismatch: 4-digit ZIP with DE country ────────────────────────────────

    def test_four_digit_zip_with_de_country_warns(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="3000", dest_country="DE")]
        result = self.svc.validate_zip_countries(inputs)
        assert result.status == "pass"  # warn does not escalate to fail/review
        assert len(result.violations) == 1
        v = result.violations[0]
        assert v.rule == "zip_country_mismatch"
        assert v.action == "warn"
        assert v.index == 0
        assert "3000" in v.detail
        assert "DE" in v.detail

    def test_four_digit_zip_with_none_country_warns(self) -> None:
        # None country defaults to DE assumption inside the validator
        inputs = [ShipmentCountryInput(index=0, dest_zip="1234", dest_country=None)]
        result = self.svc.validate_zip_countries(inputs)
        assert len(result.violations) == 1
        assert result.violations[0].rule == "zip_country_mismatch"

    def test_four_digit_origin_zip_with_de_warns(self) -> None:
        inputs = [ShipmentCountryInput(index=0, origin_zip="1010", origin_country="DE")]
        result = self.svc.validate_zip_countries(inputs)
        assert len(result.violations) == 1
        assert "origin" in result.violations[0].detail

    # ── Mismatch: 5-digit ZIP with 4-digit country ────────────────────────────

    def test_five_digit_zip_with_at_country_warns(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="10115", dest_country="AT")]
        result = self.svc.validate_zip_countries(inputs)
        assert len(result.violations) == 1
        v = result.violations[0]
        assert v.rule == "zip_country_mismatch"
        assert "AT" in v.detail

    def test_five_digit_zip_with_ch_country_warns(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="80331", dest_country="CH")]
        result = self.svc.validate_zip_countries(inputs)
        assert len(result.violations) == 1

    # ── Multiple shipments ────────────────────────────────────────────────────

    def test_multiple_shipments_each_flagged_independently(self) -> None:
        inputs = [
            ShipmentCountryInput(index=0, dest_zip="10115", dest_country="DE"),  # OK
            ShipmentCountryInput(index=1, dest_zip="3000", dest_country="DE"),   # warn
            ShipmentCountryInput(index=2, dest_zip="1010", dest_country="AT"),   # OK
            ShipmentCountryInput(index=3, dest_zip="1000", dest_country="DE"),   # warn
        ]
        result = self.svc.validate_zip_countries(inputs)
        assert len(result.violations) == 2
        flagged_indices = {v.index for v in result.violations}
        assert flagged_indices == {1, 3}

    def test_both_origin_and_dest_flagged_for_same_shipment(self) -> None:
        inputs = [
            ShipmentCountryInput(
                index=0,
                origin_zip="1010",
                origin_country="DE",
                dest_zip="3000",
                dest_country="DE",
            )
        ]
        result = self.svc.validate_zip_countries(inputs)
        assert len(result.violations) == 2

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_input_returns_pass(self) -> None:
        result = self.svc.validate_zip_countries([])
        assert result.status == "pass"
        assert result.violations == []

    def test_country_comparison_is_case_insensitive(self) -> None:
        inputs = [ShipmentCountryInput(index=0, dest_zip="3000", dest_country="de")]
        result = self.svc.validate_zip_countries(inputs)
        assert len(result.violations) == 1
