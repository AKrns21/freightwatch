"""Tests for ExtractionValidatorService — port of extraction-validator.service.spec.ts.

31 tests, all deterministic (no DB, no LLM).
"""

from __future__ import annotations

from app.services.extraction_validator_service import (
    ExtractionValidatorService,
    InvoiceHeaderInput,
    InvoiceLineInput,
    ShipmentInput,
    TariffRateInput,
    TariffZoneMapInput,
)

TENANT_ID = "tenant-abc"


def make_line(**overrides) -> InvoiceLineInput:
    defaults = dict(index=1, line_total=100.0, weight_kg=10.0, dest_zip="10115", dest_country="DE")
    defaults.update(overrides)
    return InvoiceLineInput(**defaults)


# ---------------------------------------------------------------------------
# Rule 1 — invoice total reconciliation ±2%
# ---------------------------------------------------------------------------


class TestInvoiceTotalReconciliation:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    def test_passes_when_line_sum_equals_header_total(self) -> None:
        header = InvoiceHeaderInput(total_net=200.0)
        lines = [make_line(index=1, line_total=120.0), make_line(index=2, line_total=80.0)]

        result = self.svc.validate_invoice(header, lines)

        assert result.status == "pass"
        assert result.violations == []

    def test_passes_within_2_pct_tolerance(self) -> None:
        # 1000 × 2% = 20 tolerance; diff = 19 → pass
        header = InvoiceHeaderInput(total_net=1000.0)
        lines = [make_line(index=1, line_total=981.0)]

        result = self.svc.validate_invoice(header, lines)

        assert result.status == "pass"

    def test_hold_for_review_when_exceeds_2_pct(self) -> None:
        # diff = 30 > 20 tolerance
        header = InvoiceHeaderInput(total_net=1000.0)
        lines = [make_line(index=1, line_total=970.0)]

        result = self.svc.validate_invoice(header, lines)

        assert result.status == "review"
        assert len(result.violations) == 1
        assert result.violations[0].rule == "invoice_total_reconciliation"
        assert result.violations[0].action == "hold_for_review"

    def test_skips_total_check_when_header_total_net_is_none(self) -> None:
        header = InvoiceHeaderInput(total_net=None)
        lines = [make_line(index=1, line_total=100.0)]

        result = self.svc.validate_invoice(header, lines)

        total_violations = [v for v in result.violations if v.rule == "invoice_total_reconciliation"]
        assert total_violations == []


# ---------------------------------------------------------------------------
# Rule 2 — weight_kg > 0
# ---------------------------------------------------------------------------


class TestWeightPositive:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    def test_passes_when_weight_is_positive(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(weight_kg=0.5)]
        )
        assert result.status == "pass"

    def test_rejects_when_weight_kg_is_zero(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(index=3, weight_kg=0.0)]
        )

        assert result.status == "fail"
        assert result.violations[0].rule == "weight_positive"
        assert result.violations[0].action == "reject"
        assert result.violations[0].index == 3

    def test_rejects_when_weight_kg_is_negative(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(weight_kg=-5.0)]
        )

        assert result.status == "fail"
        assert result.violations[0].rule == "weight_positive"

    def test_skips_weight_check_when_weight_kg_is_none(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(weight_kg=None)]
        )

        weight_violations = [v for v in result.violations if v.rule == "weight_positive"]
        assert weight_violations == []


# ---------------------------------------------------------------------------
# Rule 3 — dest_zip format (DE only)
# ---------------------------------------------------------------------------


class TestDestZipFormat:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    def test_passes_for_valid_5_digit_german_zip(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(dest_zip="80331")]
        )
        zip_violations = [v for v in result.violations if v.rule == "dest_zip_format_de"]
        assert zip_violations == []

    def test_warns_for_zip_with_letters(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None),
            [make_line(dest_zip="ABCDE", dest_country="DE")],
        )

        assert result.status == "pass"  # warn does not escalate to fail/review
        assert result.violations[0].rule == "dest_zip_format_de"
        assert result.violations[0].action == "warn"

    def test_warns_for_zip_shorter_than_5_digits(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(dest_zip="1011")]
        )
        assert result.violations[0].rule == "dest_zip_format_de"

    def test_skips_zip_check_for_non_de_destination(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None),
            [make_line(dest_zip="W1A 1AA", dest_country="GB")],
        )
        zip_violations = [v for v in result.violations if v.rule == "dest_zip_format_de"]
        assert zip_violations == []

    def test_applies_zip_check_when_dest_country_is_none(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None),
            [make_line(dest_zip="bad", dest_country=None)],
        )
        assert result.violations[0].rule == "dest_zip_format_de"


# ---------------------------------------------------------------------------
# Rule 4 — shipment reference deduplication
# ---------------------------------------------------------------------------


class TestShipmentReferenceDedup:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    def test_passes_when_no_references_exist_in_db(self) -> None:
        shipments = [ShipmentInput(index=0, reference_number="REF-001")]

        result = self.svc.validate_shipments(shipments, existing_refs=set())

        assert result.status == "pass"

    def test_rejects_when_reference_already_exists(self) -> None:
        shipments = [ShipmentInput(index=0, reference_number="REF-001")]

        result = self.svc.validate_shipments(shipments, existing_refs={"REF-001"})

        assert result.status == "fail"
        assert result.violations[0].rule == "reference_number_dedup"
        assert result.violations[0].action == "reject"

    def test_only_rejects_duplicate_rows(self) -> None:
        shipments = [
            ShipmentInput(index=0, reference_number="REF-001"),  # duplicate
            ShipmentInput(index=1, reference_number="REF-002"),  # new
        ]

        result = self.svc.validate_shipments(shipments, existing_refs={"REF-001"})

        assert len(result.violations) == 1
        assert result.violations[0].index == 0

    def test_passes_when_all_references_are_none(self) -> None:
        shipments = [ShipmentInput(index=0, reference_number=None)]

        result = self.svc.validate_shipments(shipments, existing_refs=set())

        assert result.status == "pass"


# ---------------------------------------------------------------------------
# Rule 5 — tariff rate weight band integrity
# ---------------------------------------------------------------------------


class TestTariffRateWeightBand:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    def test_passes_when_from_less_than_to(self) -> None:
        rates = [TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=5.0)]
        result = self.svc.validate_tariff_rates(rates)
        assert result.status == "pass"

    def test_rejects_when_from_equals_to(self) -> None:
        rates = [TariffRateInput(index=0, weight_from_kg=10.0, weight_to_kg=10.0)]
        result = self.svc.validate_tariff_rates(rates)

        assert result.status == "fail"
        assert result.violations[0].rule == "weight_band_integrity"
        assert result.violations[0].action == "reject"

    def test_rejects_when_from_greater_than_to(self) -> None:
        rates = [TariffRateInput(index=2, weight_from_kg=20.0, weight_to_kg=5.0)]
        result = self.svc.validate_tariff_rates(rates)

        assert result.status == "fail"
        assert result.violations[0].index == 2

    def test_reports_all_invalid_bands(self) -> None:
        rates = [
            TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=5.0),   # ok
            TariffRateInput(index=1, weight_from_kg=10.0, weight_to_kg=5.0),  # bad
            TariffRateInput(index=2, weight_from_kg=5.0, weight_to_kg=20.0),  # ok
            TariffRateInput(index=3, weight_from_kg=20.0, weight_to_kg=20.0), # bad
        ]

        result = self.svc.validate_tariff_rates(rates)

        integrity_violations = [v for v in result.violations if v.rule == "weight_band_integrity"]
        assert len(integrity_violations) == 2
        assert [v.index for v in integrity_violations] == [1, 3]


# ---------------------------------------------------------------------------
# Rule 6 — tariff zone map PLZ prefix validity
# ---------------------------------------------------------------------------


class TestTariffZoneMapPlzPrefix:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    def test_passes_for_2_digit_prefix(self) -> None:
        entries = [TariffZoneMapInput(index=0, plz_prefix="10")]
        result = self.svc.validate_tariff_zone_map(entries)
        assert result.status == "pass"

    def test_passes_for_5_digit_prefix(self) -> None:
        entries = [TariffZoneMapInput(index=0, plz_prefix="99999")]
        result = self.svc.validate_tariff_zone_map(entries)
        assert result.status == "pass"

    def test_passes_for_single_digit_prefix(self) -> None:
        entries = [TariffZoneMapInput(index=0, plz_prefix="1")]
        result = self.svc.validate_tariff_zone_map(entries)
        assert result.status == "pass"

    def test_rejects_prefix_with_non_digit_characters(self) -> None:
        entries = [TariffZoneMapInput(index=0, plz_prefix="10A")]
        result = self.svc.validate_tariff_zone_map(entries)

        assert result.status == "fail"
        assert result.violations[0].rule == "plz_prefix_valid"
        assert result.violations[0].action == "reject"

    def test_rejects_empty_string_prefix(self) -> None:
        entries = [TariffZoneMapInput(index=0, plz_prefix="")]
        result = self.svc.validate_tariff_zone_map(entries)
        assert result.status == "fail"

    def test_rejects_prefix_longer_than_5_digits(self) -> None:
        entries = [TariffZoneMapInput(index=0, plz_prefix="123456")]
        result = self.svc.validate_tariff_zone_map(entries)
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# Rule 5 (issue #55) — weight band continuity checks
# ---------------------------------------------------------------------------


class TestTariffRateWeightBandContinuity:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    # ============================================================================
    # NO_ZERO_START
    # ============================================================================

    def test_warns_when_first_band_does_not_start_at_zero(self) -> None:
        rates = [TariffRateInput(index=0, weight_from_kg=100.0, weight_to_kg=200.0, zone=1)]
        result = self.svc.validate_tariff_rates(rates)

        assert result.status == "pass"  # warn does not escalate
        assert len(result.violations) == 1
        assert result.violations[0].rule == "weight_band_no_zero_start"
        assert result.violations[0].action == "warn"
        assert result.violations[0].index == 0

    def test_passes_when_first_band_starts_at_zero(self) -> None:
        rates = [
            TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=100.0, zone=1),
            TariffRateInput(index=1, weight_from_kg=100.0, weight_to_kg=500.0, zone=1),
        ]
        result = self.svc.validate_tariff_rates(rates)

        continuity_violations = [
            v for v in result.violations
            if v.rule in ("weight_band_no_zero_start", "weight_band_gap", "weight_band_overlap")
        ]
        assert continuity_violations == []

    # ============================================================================
    # GAP
    # ============================================================================

    def test_warns_when_gap_between_consecutive_bands(self) -> None:
        rates = [
            TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=100.0, zone=1),
            TariffRateInput(index=1, weight_from_kg=200.0, weight_to_kg=500.0, zone=1),
        ]
        result = self.svc.validate_tariff_rates(rates)

        gap_violations = [v for v in result.violations if v.rule == "weight_band_gap"]
        assert len(gap_violations) == 1
        assert gap_violations[0].action == "warn"
        assert gap_violations[0].index == 1
        assert "100.0" in gap_violations[0].detail
        assert "200.0" in gap_violations[0].detail

    def test_no_gap_violation_for_contiguous_bands(self) -> None:
        rates = [
            TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=100.0, zone=1),
            TariffRateInput(index=1, weight_from_kg=100.0, weight_to_kg=500.0, zone=1),
            TariffRateInput(index=2, weight_from_kg=500.0, weight_to_kg=1000.0, zone=1),
        ]
        result = self.svc.validate_tariff_rates(rates)

        gap_violations = [v for v in result.violations if v.rule == "weight_band_gap"]
        assert gap_violations == []

    # ============================================================================
    # OVERLAP
    # ============================================================================

    def test_warns_when_bands_overlap(self) -> None:
        rates = [
            TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=150.0, zone=1),
            TariffRateInput(index=1, weight_from_kg=100.0, weight_to_kg=500.0, zone=1),
        ]
        result = self.svc.validate_tariff_rates(rates)

        overlap_violations = [v for v in result.violations if v.rule == "weight_band_overlap"]
        assert len(overlap_violations) == 1
        assert overlap_violations[0].action == "warn"
        assert overlap_violations[0].index == 1

    # ============================================================================
    # MULTI-ZONE
    # ============================================================================

    def test_checks_each_zone_independently(self) -> None:
        rates = [
            # Zone 1: contiguous, starts at 0 — clean
            TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=100.0, zone=1),
            TariffRateInput(index=1, weight_from_kg=100.0, weight_to_kg=500.0, zone=1),
            # Zone 2: gap between 100 and 200
            TariffRateInput(index=2, weight_from_kg=0.0, weight_to_kg=100.0, zone=2),
            TariffRateInput(index=3, weight_from_kg=200.0, weight_to_kg=500.0, zone=2),
        ]
        result = self.svc.validate_tariff_rates(rates)

        gap_violations = [v for v in result.violations if v.rule == "weight_band_gap"]
        assert len(gap_violations) == 1
        assert gap_violations[0].index == 3

    def test_no_zero_start_reported_per_zone(self) -> None:
        rates = [
            # Zone 1 starts at 0 — ok
            TariffRateInput(index=0, weight_from_kg=0.0, weight_to_kg=100.0, zone=1),
            # Zone 2 starts at 50 — warn
            TariffRateInput(index=1, weight_from_kg=50.0, weight_to_kg=200.0, zone=2),
        ]
        result = self.svc.validate_tariff_rates(rates)

        no_zero = [v for v in result.violations if v.rule == "weight_band_no_zero_start"]
        assert len(no_zero) == 1
        assert no_zero[0].index == 1

    # ============================================================================
    # ORDERING — unsorted input should still work
    # ============================================================================

    def test_sorts_bands_before_checking(self) -> None:
        # Bands provided in reverse order — should still detect no gap
        rates = [
            TariffRateInput(index=0, weight_from_kg=100.0, weight_to_kg=500.0, zone=1),
            TariffRateInput(index=1, weight_from_kg=0.0, weight_to_kg=100.0, zone=1),
        ]
        result = self.svc.validate_tariff_rates(rates)

        gap_violations = [v for v in result.violations if v.rule == "weight_band_gap"]
        assert gap_violations == []


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


class TestStatusDerivation:
    def setup_method(self) -> None:
        self.svc = ExtractionValidatorService()

    def test_fail_when_any_reject_violation(self) -> None:
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(weight_kg=0.0)]
        )
        assert result.status == "fail"

    def test_review_when_only_hold_for_review_violations(self) -> None:
        # total mismatch > 2% → hold_for_review
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=1000.0), [make_line(line_total=500.0)]
        )
        assert result.status == "review"

    def test_pass_when_only_warn_violations(self) -> None:
        # bad zip → warn only
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=None), [make_line(dest_zip="bad")]
        )
        assert result.status == "pass"

    def test_fail_takes_precedence_over_review(self) -> None:
        # total mismatch (review) + weight = 0 (fail)
        result = self.svc.validate_invoice(
            InvoiceHeaderInput(total_net=1000.0),
            [make_line(index=1, line_total=500.0, weight_kg=0.0)],
        )
        assert result.status == "fail"
