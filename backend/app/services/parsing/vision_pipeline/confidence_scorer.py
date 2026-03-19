"""Stage 5 — Per-field confidence scoring (deterministic, no LLM).

Formula:
  direct_ocr_ratio  = fields tagged "direct_ocr" / total fields examined
  completeness_ratio = required fields with a non-null value / total required fields
  overall = 0.6 × direct_ocr_ratio + 0.4 × completeness_ratio

The 60/40 split reflects that OCR quality matters more than completeness
for the auto-import decision.

Port of backend_legacy/src/modules/invoice/vision-pipeline/confidence-scorer.service.ts
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

from app.services.parsing.vision_pipeline.pipeline_types import (
    AnnotatedField,
    ConfidenceScore,
    ExtractedHeader,
    ExtractedLine,
    FieldSource,
)

_REQUIRED_HEADER_FIELDS = {"invoice_number", "invoice_date", "carrier_name", "total_net_amount"}
_REQUIRED_LINE_FIELDS = {"weight_kg", "line_total", "dest_zip"}


class ConfidenceScorer:
    """Stage 5: aggregate field-level annotations into a document confidence score."""

    def score(
        self, header: ExtractedHeader, lines: list[ExtractedLine]
    ) -> ConfidenceScore:
        field_breakdown: dict[str, FieldSource] = {}
        total_fields = 0
        direct_ocr_count = 0
        required_present = 0
        required_total = 0

        # ── header fields ────────────────────────────────────────────────────
        for f in dataclass_fields(header):
            annotated: AnnotatedField = getattr(header, f.name)
            src: FieldSource = annotated.src
            is_required = f.name in _REQUIRED_HEADER_FIELDS

            field_breakdown[f"header.{f.name}"] = src
            total_fields += 1
            if src == "direct_ocr":
                direct_ocr_count += 1

            if is_required:
                required_total += 1
                if annotated.value is not None:
                    required_present += 1

        # ── line fields ───────────────────────────────────────────────────────
        for line_idx, line in enumerate(lines):
            for f in dataclass_fields(line):
                annotated = getattr(line, f.name)
                src = annotated.src
                is_required = f.name in _REQUIRED_LINE_FIELDS

                field_breakdown[f"lines[{line_idx}].{f.name}"] = src
                total_fields += 1
                if src == "direct_ocr":
                    direct_ocr_count += 1

                if is_required:
                    required_total += 1
                    if annotated.value is not None:
                        required_present += 1

        direct_ocr_ratio = direct_ocr_count / total_fields if total_fields > 0 else 0.0
        completeness_ratio = required_present / required_total if required_total > 0 else 0.0
        overall = round(0.6 * direct_ocr_ratio + 0.4 * completeness_ratio, 2)

        return ConfidenceScore(
            overall=overall,
            direct_ocr_ratio=round(direct_ocr_ratio, 2),
            completeness_ratio=round(completeness_ratio, 2),
            field_breakdown=field_breakdown,
        )
