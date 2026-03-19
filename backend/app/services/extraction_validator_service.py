"""ExtractionValidatorService — deterministic post-parse, pre-import validator.

Port of backend_legacy/src/modules/upload/extraction-validator.service.ts

No LLM, no magic numbers. Applies six deterministic rules across all parser
outputs (invoice, CSV shipment list, tariff rate table, zone map).

Rule table:
  invoice_line    | sum(line_total) ≈ header.total_net ±2%      | hold_for_review
  invoice_line    | weight_kg > 0                               | reject
  invoice_line    | dest_zip matches ^\\d{5}$ (DE only)          | warn
  shipment        | reference_number not in existing set (dedup) | reject
  tariff_rate     | weight_from_kg < weight_to_kg               | reject
  tariff_zone_map | plz_prefix matches ^\\d{1,5}$               | reject
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Constants (identical to TypeScript originals)
# ---------------------------------------------------------------------------

INVOICE_TOTAL_TOLERANCE_PCT: float = 0.02
_DE_ZIP_PATTERN = re.compile(r"^\d{5}$")
_PLZ_PREFIX_PATTERN = re.compile(r"^\d{1,5}$")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ValidationAction = Literal["reject", "hold_for_review", "warn"]
ValidationStatus = Literal["pass", "review", "fail"]
EntityType = Literal["invoice_line", "shipment", "tariff_rate", "tariff_zone_map"]


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


@dataclass
class TariffZoneMapInput:
    index: int
    plz_prefix: str


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

        Rule 5 — weight_from_kg must be strictly less than weight_to_kg.
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
