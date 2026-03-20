"""ExtractionValidatorService — deterministic post-parse, pre-import validator.

Port of backend_legacy/src/modules/upload/extraction-validator.service.ts

No LLM, no magic numbers. Applies deterministic rules across all parser
outputs (invoice, CSV shipment list, tariff rate table, zone map).

Rule table:
  invoice_line    | sum(line_total) ≈ header.total_net ±2%      | hold_for_review
  invoice_line    | weight_kg > 0                               | reject
  invoice_line    | dest_zip matches ^\\d{5}$ (DE only)          | warn
  shipment        | reference_number not in existing set (dedup) | reject
  tariff_rate     | weight_from_kg < weight_to_kg               | reject
  tariff_rate     | no gap between consecutive bands (per zone)  | warn  (issue #55)
  tariff_rate     | no overlap between consecutive bands (zone)  | warn  (issue #55)
  tariff_rate     | first band starts at 0 (per zone)            | warn  (issue #55)
  tariff_zone_map | plz_prefix matches ^\\d{1,5}$               | reject
  shipment        | ZIP length inconsistent with country field   | warn  (issue #54)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants (identical to TypeScript originals)
# ---------------------------------------------------------------------------

INVOICE_TOTAL_TOLERANCE_PCT: float = 0.02
_DE_ZIP_PATTERN = re.compile(r"^\d{5}$")
_PLZ_PREFIX_PATTERN = re.compile(r"^\d{1,5}$")
_FOUR_DIGIT_ZIP_PATTERN = re.compile(r"^\d{4}$")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ValidationAction = Literal["reject", "hold_for_review", "warn"]
ValidationStatus = Literal["pass", "review", "fail"]
EntityType = Literal["invoice_line", "shipment", "tariff_rate", "tariff_zone_map"]

# Countries whose postal codes are 5 digits (safe to infer from length alone)
_FIVE_DIGIT_COUNTRIES = {"DE", "FR", "ES", "IT", "US"}
# Countries whose postal codes are 4 digits — cannot distinguish without context
_FOUR_DIGIT_COUNTRIES = {"AT", "CH", "BE", "LU", "HU", "NL", "AU", "DK", "NO"}


@dataclass
class ValidationViolation:
    entity: EntityType
    rule: str
    action: ValidationAction
    detail: str
    index: int | None = None


@dataclass
class ExtractionValidationResult:
    status: ValidationStatus
    violations: list[ValidationViolation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Input dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InvoiceHeaderInput:
    total_net: float | None


@dataclass
class InvoiceLineInput:
    index: int
    line_total: float | None = None
    weight_kg: float | None = None
    dest_zip: str | None = None
    dest_country: str | None = None


@dataclass
class ShipmentInput:
    index: int
    reference_number: str | None = None


@dataclass
class TariffRateInput:
    index: int
    weight_from_kg: float
    weight_to_kg: float
    zone: int | str | None = None


@dataclass
class TariffZoneMapInput:
    index: int
    plz_prefix: str


@dataclass
class ShipmentCountryInput:
    """ZIP + country pair for one shipment (issue #54)."""

    index: int
    origin_zip: str | None = None
    origin_country: str | None = None
    dest_zip: str | None = None
    dest_country: str | None = None


# ---------------------------------------------------------------------------
# Public helper — also used directly in unit tests
# ---------------------------------------------------------------------------


def infer_country_from_zip(zip_str: str | None) -> str | None:
    """Infer ISO-2 country code from ZIP code format.

    Rules (deterministic, no external data):
      - 5 digits → DE  (German PLZ are always 5 digits)
      - 4 digits → None  (ambiguous: AT / CH / BE / LU / HU / …)
      - anything else → None

    A return value of None means the country cannot be determined from the
    ZIP alone and a warning should be raised.

    Args:
        zip_str: Raw ZIP code string (may be None or empty).

    Returns:
        ISO-2 country code string, or None if ambiguous / unknown.
    """
    if not zip_str:
        return None
    clean = zip_str.strip()
    if _DE_ZIP_PATTERN.match(clean):
        return "DE"
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _derive_status(violations: list[ValidationViolation]) -> ValidationStatus:
    if any(v.action == "reject" for v in violations):
        return "fail"
    if any(v.action == "hold_for_review" for v in violations):
        return "review"
    return "pass"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class ExtractionValidatorService:
    """Shared post-parse, pre-import validator.

    All methods are pure functions — no DB session required. The caller is
    responsible for pre-fetching data needed for deduplication checks and
    passing it in directly (``existing_refs`` in ``validate_shipments``).
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    # ------------------------------------------------------------------
    # Rule 1-3: Invoice validation
    # ------------------------------------------------------------------

    def validate_invoice(
        self,
        header: InvoiceHeaderInput,
        lines: list[InvoiceLineInput],
    ) -> ExtractionValidationResult:
        """Validate invoice lines against the header total and per-line rules.

        Rules applied:
          1. sum(line_total) ≈ header.total_net ±2%  → hold_for_review
          2. weight_kg > 0                            → reject (per line)
          3. dest_zip format (DE only)                → warn (per line)
        """
        violations: list[ValidationViolation] = []

        # Rule 1 — total reconciliation ±2%
        if header.total_net is not None and lines:
            line_sum = sum(line.line_total or 0.0 for line in lines)
            tolerance = abs(header.total_net) * INVOICE_TOTAL_TOLERANCE_PCT
            diff = abs(header.total_net - line_sum)

            if diff > tolerance:
                violations.append(
                    ValidationViolation(
                        entity="invoice_line",
                        rule="invoice_total_reconciliation",
                        action="hold_for_review",
                        detail=(
                            f"Header total {header.total_net:.2f} differs from "
                            f"sum of lines {line_sum:.2f} by {diff:.2f} "
                            f"(tolerance {tolerance:.2f}, "
                            f"{INVOICE_TOTAL_TOLERANCE_PCT * 100:.0f}%)"
                        ),
                    )
                )

        for line in lines:
            # Rule 2 — weight must be positive
            if line.weight_kg is not None and line.weight_kg <= 0:
                violations.append(
                    ValidationViolation(
                        entity="invoice_line",
                        rule="weight_positive",
                        action="reject",
                        detail=f"Line {line.index}: weight_kg is {line.weight_kg} (must be > 0)",
                        index=line.index,
                    )
                )

            # Rule 3 — dest_zip format for DE shipments
            is_de = (
                line.dest_country is None
                or line.dest_country.upper() == "DE"
            )
            if is_de and line.dest_zip is not None and not _DE_ZIP_PATTERN.match(line.dest_zip):
                violations.append(
                    ValidationViolation(
                        entity="invoice_line",
                        rule="dest_zip_format_de",
                        action="warn",
                        detail=(
                            f'Line {line.index}: dest_zip "{line.dest_zip}" '
                            r"does not match /^\d{5}$/"
                        ),
                        index=line.index,
                    )
                )

        return ExtractionValidationResult(
            status=_derive_status(violations),
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Rule 4: Shipment reference deduplication
    # ------------------------------------------------------------------

    def validate_shipments(
        self,
        shipments: list[ShipmentInput],
        existing_refs: set[str],
    ) -> ExtractionValidationResult:
        """Validate parsed shipments against a pre-fetched set of known references.

        Rule 4 — if reference_number already exists (for this tenant), reject.

        Args:
            shipments: Shipments to validate.
            existing_refs: Set of reference_number strings already in the DB
                           for this tenant. The caller is responsible for
                           fetching these before calling this method.
        """
        violations: list[ValidationViolation] = []

        for shipment in shipments:
            ref = shipment.reference_number
            if ref and ref.strip() and ref in existing_refs:
                violations.append(
                    ValidationViolation(
                        entity="shipment",
                        rule="reference_number_dedup",
                        action="reject",
                        detail=(
                            f'Shipment {shipment.index}: reference_number "{ref}" '
                            "already exists for tenant"
                        ),
                        index=shipment.index,
                    )
                )

        return ExtractionValidationResult(
            status=_derive_status(violations),
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Rule 5: Tariff rate weight band integrity
    # ------------------------------------------------------------------

    def validate_tariff_rates(
        self,
        rates: list[TariffRateInput],
    ) -> ExtractionValidationResult:
        """Validate tariff rate weight bands.

        Rule 5 — weight_from_kg must be strictly less than weight_to_kg (reject).
        Rules from issue #55 (warn, per-zone):
          - weight_band_no_zero_start : first band in a zone does not start at 0
          - weight_band_gap           : gap between consecutive bands
          - weight_band_overlap       : overlap between consecutive bands
        """
        violations: list[ValidationViolation] = []

        for rate in rates:
            if rate.weight_from_kg >= rate.weight_to_kg:
                violations.append(
                    ValidationViolation(
                        entity="tariff_rate",
                        rule="weight_band_integrity",
                        action="reject",
                        detail=(
                            f"Rate {rate.index}: weight_from_kg ({rate.weight_from_kg}) "
                            f"must be < weight_to_kg ({rate.weight_to_kg})"
                        ),
                        index=rate.index,
                    )
                )

        # Group by zone, sort each group by weight_from_kg, check continuity.
        zones: dict[int | str | None, list[TariffRateInput]] = {}
        for rate in rates:
            zones.setdefault(rate.zone, []).append(rate)

        for zone_key, zone_rates in zones.items():
            sorted_rates = sorted(zone_rates, key=lambda r: r.weight_from_kg)
            zone_label = f"zone={zone_key}" if zone_key is not None else "zone=<unset>"

            first = sorted_rates[0]
            if first.weight_from_kg != 0:
                violations.append(
                    ValidationViolation(
                        entity="tariff_rate",
                        rule="weight_band_no_zero_start",
                        action="warn",
                        detail=(
                            f"Rate {first.index} ({zone_label}): first band starts at "
                            f"{first.weight_from_kg} kg, not 0 — weights below "
                            f"{first.weight_from_kg} kg are unmatched"
                        ),
                        index=first.index,
                    )
                )

            for prev, curr in zip(sorted_rates, sorted_rates[1:]):
                if curr.weight_from_kg > prev.weight_to_kg:
                    violations.append(
                        ValidationViolation(
                            entity="tariff_rate",
                            rule="weight_band_gap",
                            action="warn",
                            detail=(
                                f"Rate {curr.index} ({zone_label}): gap between bands — "
                                f"{prev.weight_to_kg}–{curr.weight_from_kg} kg is unmatched"
                            ),
                            index=curr.index,
                        )
                    )
                elif curr.weight_from_kg < prev.weight_to_kg:
                    violations.append(
                        ValidationViolation(
                            entity="tariff_rate",
                            rule="weight_band_overlap",
                            action="warn",
                            detail=(
                                f"Rate {curr.index} ({zone_label}): overlap — "
                                f"band [{prev.weight_from_kg}, {prev.weight_to_kg}) and "
                                f"[{curr.weight_from_kg}, {curr.weight_to_kg}) overlap "
                                f"at {curr.weight_from_kg}–{prev.weight_to_kg} kg"
                            ),
                            index=curr.index,
                        )
                    )

        return ExtractionValidationResult(
            status=_derive_status(violations),
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Rule 7: ZIP / country consistency  (issue #54)
    # ------------------------------------------------------------------

    def validate_zip_countries(
        self,
        inputs: list[ShipmentCountryInput],
    ) -> ExtractionValidationResult:
        """Warn when ZIP length is inconsistent with the country field.

        Rules applied per shipment (both origin and dest):
          - 4-digit ZIP with country == "DE" or None
            → warn: DE postal codes are always 5 digits; likely AT/CH/BE
          - 5-digit ZIP with country in _FOUR_DIGIT_COUNTRIES
            → warn: country uses 4-digit codes; ZIP may be wrong
          - 5-digit ZIP with country "DE" (or None)
            → consistent, no violation

        Args:
            inputs: List of ShipmentCountryInput, one per parsed shipment.

        Returns:
            ExtractionValidationResult with warn-level violations only.
        """
        violations: list[ValidationViolation] = []

        for inp in inputs:
            for side, zip_val, country_val in [
                ("origin", inp.origin_zip, inp.origin_country),
                ("dest", inp.dest_zip, inp.dest_country),
            ]:
                if not zip_val:
                    continue
                clean = zip_val.strip()
                country_upper = (country_val or "DE").upper()

                if _FOUR_DIGIT_ZIP_PATTERN.match(clean) and country_upper == "DE":
                    violations.append(
                        ValidationViolation(
                            entity="shipment",
                            rule="zip_country_mismatch",
                            action="warn",
                            detail=(
                                f"Shipment {inp.index}: {side}_zip '{clean}' is 4 digits "
                                f"but country is '{country_upper}' — "
                                "German PLZ are always 5 digits; possible AT/CH/BE"
                            ),
                            index=inp.index,
                        )
                    )
                elif _DE_ZIP_PATTERN.match(clean) and country_upper in _FOUR_DIGIT_COUNTRIES:
                    violations.append(
                        ValidationViolation(
                            entity="shipment",
                            rule="zip_country_mismatch",
                            action="warn",
                            detail=(
                                f"Shipment {inp.index}: {side}_zip '{clean}' is 5 digits "
                                f"but country '{country_upper}' uses 4-digit postal codes"
                            ),
                            index=inp.index,
                        )
                    )

        return ExtractionValidationResult(
            status=_derive_status(violations),
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Rule 6: Tariff zone map PLZ prefix validity
    # ------------------------------------------------------------------

    def validate_tariff_zone_map(
        self,
        entries: list[TariffZoneMapInput],
    ) -> ExtractionValidationResult:
        """Validate tariff zone map PLZ prefixes.

        Rule 6 — plz_prefix must be 1–5 digits (range 0–99999).
        """
        violations: list[ValidationViolation] = []

        for entry in entries:
            if not _PLZ_PREFIX_PATTERN.match(entry.plz_prefix):
                violations.append(
                    ValidationViolation(
                        entity="tariff_zone_map",
                        rule="plz_prefix_valid",
                        action="reject",
                        detail=(
                            f'Entry {entry.index}: plz_prefix "{entry.plz_prefix}" is not a valid '
                            "German postal code prefix (expected 1–5 digits, 00000–99999)"
                        ),
                        index=entry.index,
                    )
                )

        return ExtractionValidationResult(
            status=_derive_status(violations),
            violations=violations,
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_extraction_validator_service: ExtractionValidatorService | None = None


def get_extraction_validator_service() -> ExtractionValidatorService:
    """Return the module-level ExtractionValidatorService singleton."""
    global _extraction_validator_service
    if _extraction_validator_service is None:
        _extraction_validator_service = ExtractionValidatorService()
    return _extraction_validator_service
