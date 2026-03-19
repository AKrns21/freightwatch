"""InvoiceParserService — Parse carrier invoice PDFs.

Strategy:
  1. Detect text vs. scanned (vision) mode via DocumentService
  2. For text PDFs: find matching ParseTemplate, extract via regex/template rules
  3. For vision PDFs: run 6-stage VisionPipeline
  4. Text-mode fallback: LLM extraction (if no template matches)

Port of backend_legacy/src/modules/invoice/invoice-parser.service.ts
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ParsingTemplate, Upload
from app.services.document_service import DocumentService
from app.services.parsing.vision_pipeline.vision_pipeline import VisionPipeline
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class InvoiceHeader:
    invoice_number: str
    invoice_date: str | None        # YYYY-MM-DD string
    carrier_name: str
    carrier_id: str | None = None
    customer_name: str | None = None
    customer_number: str | None = None
    total_amount: float | None = None
    currency: str = "EUR"
    payment_terms: str | None = None
    due_date: str | None = None


@dataclass
class InvoiceLine:
    currency: str = "EUR"
    line_number: int | None = None
    shipment_date: str | None = None   # YYYY-MM-DD
    shipment_reference: str | None = None
    billing_type: str | None = None
    tour_number: str | None = None
    referenz: str | None = None
    origin_zip: str | None = None
    origin_country: str | None = None
    dest_zip: str | None = None
    dest_country: str | None = None
    weight_kg: float | None = None
    service_level: str | None = None
    base_amount: float | None = None
    diesel_amount: float | None = None
    toll_amount: float | None = None
    other_charges: float | None = None
    line_total: float | None = None


@dataclass
class InvoiceParseResult:
    header: InvoiceHeader
    lines: list[InvoiceLine]
    parsing_method: str   # 'template' | 'llm' | 'hybrid'
    confidence: float
    issues: list[str] = field(default_factory=list)


class InvoiceParserService:
    """Parse carrier invoice PDFs and return structured header + line items."""

    def __init__(self) -> None:
        self._doc_service = DocumentService()
        self._vision_pipeline = VisionPipeline()

    async def parse_invoice_pdf_multi(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        carrier_id: str | None = None,
        tenant_id: UUID,
        upload_id: UUID | None = None,
        project_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> list[InvoiceParseResult]:
        """Parse invoice PDF; returns one result per detected invoice.

        For scanned (vision) PDFs that contain multiple stapled invoices this
        returns one InvoiceParseResult per invoice. For all other paths a
        single-element array is returned so callers always get an array.
        """
        logger.info(
            "parse_invoice_pdf_start",
            filename=filename,
            carrier_id=carrier_id,
        )

        doc = await self._doc_service.process(file_bytes, filename=filename)

        logger.info(
            "pdf_extraction_complete",
            filename=filename,
            mode=doc.mode,
            page_count=doc.page_count,
        )

        # ── Vision path (scanned PDF / image) ──────────────────────────────
        if doc.mode == "vision":
            result = await self._vision_pipeline.run(
                doc.pages,
                carrier_id=carrier_id,
                tenant_id=tenant_id,
                upload_id=upload_id,
                db=db,
            )
            compat = self._vision_pipeline.to_parser_compatible(result)

            logger.info(
                "parse_invoice_vision_pipeline_complete",
                filename=filename,
                confidence=compat["confidence"],
                review_action=compat["review_action"],
                line_count=len(compat["lines"]),
            )

            return [self._compat_to_parse_result(compat)]

        # ── Text path: try template ─────────────────────────────────────────
        if doc.mode == "text" and db is not None:
            template = await self._find_matching_template(
                db, filename=filename, carrier_id=carrier_id, tenant_id=tenant_id
            )
            if template is not None:
                try:
                    parse_result = self._parse_with_template(
                        doc.text or "", template
                    )
                    logger.info(
                        "parse_invoice_template_success",
                        filename=filename,
                        template_id=str(template.id),
                        line_count=len(parse_result.lines),
                    )
                    return [parse_result]
                except Exception as exc:
                    logger.warning(
                        "parse_invoice_template_failed",
                        filename=filename,
                        template_id=str(template.id),
                        error=str(exc),
                    )
                    # Fall through to LLM

        # ── Text-mode LLM fallback (stub — full LLM parser in Phase 4) ─────
        logger.warning(
            "parse_invoice_no_template_no_vision",
            filename=filename,
            mode=doc.mode,
        )
        return [
            InvoiceParseResult(
                header=InvoiceHeader(
                    invoice_number="UNKNOWN",
                    invoice_date=None,
                    carrier_name="Unknown",
                    currency="EUR",
                ),
                lines=[],
                parsing_method="llm",
                confidence=0.0,
                issues=["No template matched and LLM fallback not yet implemented"],
            )
        ]

    async def parse_invoice_pdf(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        carrier_id: str | None = None,
        tenant_id: UUID,
        upload_id: UUID | None = None,
        project_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> InvoiceParseResult:
        """Parse and return only the first detected invoice (convenience wrapper)."""
        results = await self.parse_invoice_pdf_multi(
            file_bytes,
            filename=filename,
            carrier_id=carrier_id,
            tenant_id=tenant_id,
            upload_id=upload_id,
            project_id=project_id,
            db=db,
        )
        return results[0]

    # ── template matching ─────────────────────────────────────────────────────

    async def _find_matching_template(
        self,
        db: AsyncSession,
        *,
        filename: str,
        carrier_id: str | None,
        tenant_id: UUID,
    ) -> ParsingTemplate | None:
        result = await db.execute(
            select(ParsingTemplate)
            .where(
                ParsingTemplate.template_category == "invoice",
                ParsingTemplate.deleted_at.is_(None),
            )
            .order_by(ParsingTemplate.usage_count.desc())
        )
        templates = result.scalars().all()

        best_match: ParsingTemplate | None = None
        best_score = 0.0

        for tmpl in templates:
            score = 0.0
            detection: dict = tmpl.detection or {}

            if carrier_id and detection.get("carrier_id") == carrier_id:
                score += 0.5

            if detection.get("filename_pattern"):
                try:
                    if re.search(detection["filename_pattern"], filename, re.IGNORECASE):
                        score += 0.3
                except re.error:
                    pass

            if tmpl.tenant_id == tenant_id:
                score += 0.1

            if (tmpl.usage_count or 0) > 5:
                score += 0.1

            if score > best_score:
                best_score = score
                best_match = tmpl

        return best_match if best_score >= 0.7 else None

    # ── template-based parsing (text PDFs) ───────────────────────────────────

    def _parse_with_template(
        self, pdf_text: str, template: ParsingTemplate
    ) -> InvoiceParseResult:
        mappings: dict = template.mappings or {}
        header = self._extract_header_with_template(pdf_text, mappings.get("header", {}))
        lines = self._extract_lines_with_template(pdf_text, mappings.get("lines", {}))
        confidence = self._calculate_confidence(lines)

        return InvoiceParseResult(
            header=header,
            lines=lines,
            parsing_method="template",
            confidence=confidence,
        )

    def _extract_header_with_template(
        self, pdf_text: str, header_mappings: dict
    ) -> InvoiceHeader:
        # TODO: implement regex-based header extraction from mappings
        logger.warning("template_header_extraction_not_implemented")
        return InvoiceHeader(
            invoice_number="UNKNOWN",
            invoice_date=None,
            carrier_name="Unknown",
            currency="EUR",
        )

    def _extract_lines_with_template(
        self, pdf_text: str, line_mappings: dict
    ) -> list[InvoiceLine]:
        # TODO: implement regex/table-based line extraction from mappings
        logger.warning("template_line_extraction_not_implemented")
        return []

    def _calculate_confidence(self, lines: list[InvoiceLine]) -> float:
        """Fraction of lines with all required fields (weight, at least one zip, non-zero total)."""
        if not lines:
            return 0.0
        complete = sum(
            1
            for line in lines
            if line.weight_kg is not None
            and (line.origin_zip is not None or line.dest_zip is not None)
            and ((line.line_total or 0) != 0 or (line.base_amount or 0) != 0)
        )
        return min(1.0, max(0.0, complete / len(lines)))

    # ── conversion helpers ────────────────────────────────────────────────────

    def _compat_to_parse_result(self, compat: dict) -> InvoiceParseResult:
        h = compat["header"]
        return InvoiceParseResult(
            header=InvoiceHeader(
                invoice_number=h.get("invoice_number") or "UNKNOWN",
                invoice_date=h.get("invoice_date"),
                carrier_name=h.get("carrier_name") or "Unknown",
                customer_name=h.get("customer_name"),
                customer_number=h.get("customer_number"),
                total_amount=h.get("total_amount"),
                currency=h.get("currency") or "EUR",
            ),
            lines=[
                InvoiceLine(
                    line_number=line.get("line_number"),
                    shipment_date=line.get("shipment_date"),
                    shipment_reference=line.get("shipment_reference"),
                    billing_type=line.get("billing_type"),
                    tour_number=line.get("tour_number"),
                    referenz=line.get("referenz"),
                    origin_zip=line.get("origin_zip"),
                    origin_country=line.get("origin_country") or "DE",
                    dest_zip=line.get("dest_zip"),
                    dest_country=line.get("dest_country") or "DE",
                    weight_kg=line.get("weight_kg"),
                    base_amount=line.get("base_amount"),
                    line_total=line.get("line_total"),
                    currency=h.get("currency") or "EUR",
                )
                for line in compat["lines"]
            ],
            parsing_method="llm",
            confidence=compat["confidence"],
            issues=compat["issues"],
        )
