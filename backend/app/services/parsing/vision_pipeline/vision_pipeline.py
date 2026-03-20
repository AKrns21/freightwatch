"""VisionPipeline — Orchestrator for the 6-stage invoice vision pipeline.

  Stage 1 — PreProcessor:             grayscale + normalize + sharpen
  Stage 2 — PageClassifier:           cover | line-item-table | surcharge-appendix | continuation
  Stage 3 — StructuredExtractor:      per-page Claude Sonnet extraction with field-src annotations
  Stage 4 — CrossDocumentValidator:   total reconciliation + required fields + date/weight sanity
  Stage 5 — ConfidenceScorer:         direct_ocr_ratio + completeness_ratio → overall score
  Stage 6 — ReviewGate:               threshold routing + GoBD raw_extraction audit trail

Returns PipelineResult — callers convert this to InvoiceParseResult for DB import.

Port of backend_legacy/src/modules/invoice/vision-pipeline/vision-pipeline.service.ts
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.document_service import PageImage
from app.services.parsing.vision_pipeline.confidence_scorer import ConfidenceScorer
from app.services.parsing.vision_pipeline.cross_document_validator import (
    CrossDocumentValidator,
)
from app.services.parsing.vision_pipeline.page_classifier import PageClassifier
from app.services.parsing.vision_pipeline.pipeline_types import (
    AnnotatedField,
    ExtractedHeader,
    ExtractedLine,
    PageExtractionResult,
    PipelineResult,
    ReviewAction,
)
from app.services.parsing.vision_pipeline.pre_processor import PreProcessor
from app.services.parsing.vision_pipeline.review_gate import ReviewGate
from app.services.parsing.vision_pipeline.structured_extractor import StructuredExtractor
from app.utils.logger import get_logger

logger = get_logger(__name__)


class VisionPipeline:
    """Orchestrates all 6 stages of the invoice vision parsing pipeline."""

    def __init__(self) -> None:
        self._pre_processor = PreProcessor()
        self._page_classifier = PageClassifier()
        self._structured_extractor = StructuredExtractor()
        self._validator = CrossDocumentValidator()
        self._confidence_scorer = ConfidenceScorer()
        self._review_gate = ReviewGate()

    async def run(
        self,
        raw_pages: list[PageImage],
        *,
        carrier_id: str | None = None,
        tenant_id: UUID | None = None,
        upload_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> PipelineResult:
        logger.info(
            "vision_pipeline_start",
            page_count=len(raw_pages),
            carrier_id=carrier_id,
            upload_id=str(upload_id) if upload_id else None,
        )

        # Stage 1: Pre-processing
        processed_pages = await self._pre_processor.process_pages(raw_pages)

        # Stage 2: Page classification
        classified_pages = await self._page_classifier.classify_pages(processed_pages)

        # Stage 3: Structured extraction (per page)
        page_results = await self._structured_extractor.extract_pages(
            classified_pages, carrier_id=carrier_id
        )

        # Merge: best header + all lines
        header, lines, issues = self._merge_page_results(page_results)

        # Stage 4: Cross-document validation
        validation = self._validator.validate(header, lines)

        # Stage 5: Confidence scoring
        confidence = self._confidence_scorer.score(header, lines)

        # Stage 6: Review gate
        review_action = self._review_gate.decide(confidence, validation)

        # Persist raw extraction audit trail (non-blocking, best-effort)
        if tenant_id and upload_id and db is not None:
            await self._review_gate.persist_raw_extraction(
                db,
                tenant_id=tenant_id,
                upload_id=upload_id,
                payload={
                    "header": self._header_to_dict(header),
                    "lines": [self._line_to_dict(line) for line in lines],
                    "page_results": [
                        {
                            "page_number": pr.page_number,
                            "page_type": pr.page_type,
                            "raw_issues": pr.raw_issues,
                        }
                        for pr in page_results
                    ],
                },
                confidence=confidence.overall,
                issues=[*issues, *validation.errors, *validation.warnings],
            )

        all_issues = [
            *issues,
            *[f"[ERROR] {e}" for e in validation.errors],
            *[f"[WARN] {w}" for w in validation.warnings],
        ]

        logger.info(
            "vision_pipeline_complete",
            confidence=confidence.overall,
            direct_ocr_ratio=confidence.direct_ocr_ratio,
            line_count=len(lines),
            review_action=review_action,
            validation_errors=len(validation.errors),
            validation_warnings=len(validation.warnings),
        )

        return PipelineResult(
            header=header,
            lines=lines,
            confidence=confidence,
            validation=validation,
            review_action=review_action,
            all_issues=all_issues,
        )

    # ── merge helper ──────────────────────────────────────────────────────────

    def _merge_page_results(
        self, pages: list[PageExtractionResult]
    ) -> tuple[ExtractedHeader, list[ExtractedLine], list[str]]:
        all_issues: list[str] = []

        headers_with_number = [
            p.header
            for p in pages
            if p.header is not None and p.header.invoice_number.value is not None
        ]
        all_headers = [p.header for p in pages if p.header is not None]

        primary_header = (
            headers_with_number[0] if headers_with_number else (all_headers[0] if all_headers else None)
        )

        if primary_header is None:
            all_issues.append("No header information found in any page")
            return self._empty_header(), [], all_issues

        # Merge: fill nulls in primary header from subsequent pages
        merged = _clone_header(primary_header)
        remaining = (headers_with_number + all_headers)[1:]

        for h in remaining:
            for f in dataclass_fields(merged):
                cur: AnnotatedField = getattr(merged, f.name)
                alt: AnnotatedField = getattr(h, f.name)
                if cur.value is None and alt.value is not None:
                    setattr(merged, f.name, alt)

        # Collect lines from all non-cover pages
        lines: list[ExtractedLine] = []
        for page in pages:
            if page.page_type != "cover":
                lines.extend(page.lines)
            all_issues.extend(page.raw_issues)

        return merged, lines, all_issues

    def _empty_header(self) -> ExtractedHeader:
        missing: AnnotatedField[None] = AnnotatedField(value=None, src="missing")
        return ExtractedHeader(
            invoice_number=AnnotatedField(value=None, src="missing"),
            invoice_date=AnnotatedField(value=None, src="missing"),
            carrier_name=AnnotatedField(value=None, src="missing"),
            customer_name=AnnotatedField(value=None, src="missing"),
            customer_number=AnnotatedField(value=None, src="missing"),
            total_net_amount=AnnotatedField(value=None, src="missing"),
            total_gross_amount=AnnotatedField(value=None, src="missing"),
            currency=AnnotatedField(value="EUR", src="llm_inferred"),
        )

    # ── parser-compatible output ──────────────────────────────────────────────

    def to_parser_compatible(self, result: PipelineResult) -> dict[str, Any]:
        """Convert PipelineResult to the flat format used by InvoiceParserService."""
        h = result.header

        def val(field: AnnotatedField) -> Any:
            return field.value

        return {
            "header": {
                "invoice_number": val(h.invoice_number) or "UNKNOWN",
                "invoice_date": val(h.invoice_date),  # YYYY-MM-DD string or None
                "carrier_name": val(h.carrier_name) or "Unknown",
                "customer_name": val(h.customer_name),
                "customer_number": val(h.customer_number),
                "total_amount": val(h.total_net_amount),
                "currency": val(h.currency) or "EUR",
            },
            "lines": [
                {
                    "line_number": idx + 1,
                    "shipment_date": val(line.shipment_date),
                    "shipment_reference": val(line.shipment_reference),
                    "billing_type": val(line.billing_type),
                    "tour_number": val(line.tour),
                    "referenz": val(line.shipment_reference),
                    "origin_zip": val(line.origin_zip),
                    "origin_country": val(line.origin_country) or "DE",
                    "dest_zip": val(line.dest_zip),
                    "dest_country": val(line.dest_country) or "DE",
                    "weight_kg": val(line.weight_kg),
                    "base_amount": val(line.unit_price),
                    "line_total": val(line.line_total),
                    "currency": val(h.currency) or "EUR",
                }
                for idx, line in enumerate(result.lines)
            ],
            "confidence": result.confidence.overall,
            "issues": result.all_issues,
            "review_action": result.review_action.value,
        }

    # ── dict helpers for audit trail ──────────────────────────────────────────

    def _header_to_dict(self, h: ExtractedHeader) -> dict:
        return {
            f.name: {"value": getattr(h, f.name).value, "src": getattr(h, f.name).src}
            for f in dataclass_fields(h)
        }

    def _line_to_dict(self, line: ExtractedLine) -> dict:
        return {
            f.name: {"value": getattr(line, f.name).value, "src": getattr(line, f.name).src}
            for f in dataclass_fields(line)
        }


def _clone_header(h: ExtractedHeader) -> ExtractedHeader:
    """Shallow clone of ExtractedHeader so we can mutate without affecting original."""
    return ExtractedHeader(**{f.name: getattr(h, f.name) for f in dataclass_fields(h)})
