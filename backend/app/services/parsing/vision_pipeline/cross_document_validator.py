"""Stage 4 — Cross-document validation.

Applies deterministic checks across the merged extraction result:
  1. Required-field check  — invoice_number, invoice_date, at least one line
  2. Date plausibility     — invoice_date not in the future, not older than 3 years
  3. Total reconciliation  — sum(line_totals) ≈ header.total_net_amount ±0.02 EUR
  4. Weight sanity         — each weight_kg within realistic range

Port of backend_legacy/src/modules/invoice/vision-pipeline/cross-document-validator.service.ts
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.parsing.vision_pipeline.pipeline_types import (
    ExtractedHeader,
    ExtractedLine,
    ValidationResult,
)

_TOTAL_TOLERANCE = 0.02   # EUR
_MIN_WEIGHT_KG = 0.01
_MAX_WEIGHT_KG = 50_000
_MAX_INVOICE_AGE_MONTHS = 36


class CrossDocumentValidator:
    """Stage 4: deterministic cross-page consistency checks."""

    def validate(
        self, header: ExtractedHeader, lines: list[ExtractedLine]
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Required fields
        if not header.invoice_number.value:
            errors.append("Missing invoice_number")
        if not header.invoice_date.value:
            errors.append("Missing invoice_date")
        if not lines:
            errors.append("No line items extracted")

        # 2. Date plausibility
        if header.invoice_date.value:
            try:
                invoice_dt = datetime.fromisoformat(header.invoice_date.value).replace(
                    tzinfo=timezone.utc
                )
                now = datetime.now(tz=timezone.utc)

                if invoice_dt > now:
                    errors.append(
                        f"Invoice date {header.invoice_date.value} is in the future"
                    )

                cutoff = date(
                    now.year - (_MAX_INVOICE_AGE_MONTHS // 12),
                    now.month,
                    now.day,
                )
                # Rough 36-month window using year subtraction
                cutoff_dt = datetime(
                    now.year - 3, now.month, now.day, tzinfo=timezone.utc
                )
                if invoice_dt < cutoff_dt:
                    warnings.append(
                        f"Invoice date {header.invoice_date.value} is older than "
                        f"{_MAX_INVOICE_AGE_MONTHS} months"
                    )
            except ValueError:
                errors.append(
                    f'Unparseable invoice_date: "{header.invoice_date.value}"'
                )

        # 3. Total reconciliation
        header_total = (
            header.total_net_amount.value or header.total_gross_amount.value
        )
        if header_total is not None and lines:
            line_sum = sum(
                (line.line_total.value or 0.0) for line in lines
            )
            diff = abs(header_total - line_sum)
            if diff > _TOTAL_TOLERANCE:
                warnings.append(
                    f"Total mismatch: header says {header_total:.2f} EUR, "
                    f"sum of lines = {line_sum:.2f} EUR "
                    f"(diff {diff:.2f} EUR)"
                )

        # 4. Weight sanity per line
        for i, line in enumerate(lines):
            w = line.weight_kg.value
            if w is not None:
                if w < _MIN_WEIGHT_KG or w > _MAX_WEIGHT_KG:
                    warnings.append(
                        f"Line {i + 1}: weight_kg {w} is outside plausible range "
                        f"({_MIN_WEIGHT_KG}–{_MAX_WEIGHT_KG} kg)"
                    )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
