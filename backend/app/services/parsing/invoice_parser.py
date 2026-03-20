"""InvoiceParserService — Parse carrier invoice PDFs.

Strategy:
  1. Detect text vs. scanned (vision) mode via DocumentService
  2. For text PDFs: find matching ParseTemplate, extract via regex/template rules
  3. For vision PDFs: run 6-stage VisionPipeline
  4. Text-mode fallback: LLM extraction via Claude (freight_invoice_extractor v1.0.0)

Port of backend_legacy/src/modules/invoice/invoice-parser.service.ts
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from uuid import UUID

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import ParsingTemplate
from app.services.document_service import DocumentService
from app.services.parsing.vision_pipeline.vision_pipeline import VisionPipeline
from app.services.prompts.versions import get_prompt_version
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Confidence thresholds (mirror ReviewGate logic)
_THRESHOLD_AUTO_IMPORT = 0.90
_THRESHOLD_AUTO_FLAG = 0.75
_THRESHOLD_HOLD = 0.50


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
    invoice_number: str | None = None
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
    # Routing decision: 'auto_import' | 'auto_import_flag' | 'hold_for_review' | 'reject'
    review_action: str = "hold_for_review"


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

        # ── Vision path: OCR already done by DocumentService — use combined text ──
        # Processing pages individually loses cross-page context and misses shipments
        # on blank/continuation pages. The OCR text from all pages is in doc.text;
        # passing it to the LLM fallback gives a holistic extraction in one call.
        if doc.mode == "vision":
            logger.info(
                "parse_invoice_vision_text_fallback",
                filename=filename,
                text_len=len(doc.text or ""),
                prompt_version=settings.invoice_extractor_prompt_version,
            )
            return [await self._text_llm_fallback(doc.text or "", filename=filename)]

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

        # ── Text-mode LLM fallback (freight_invoice_extractor, versioned prompt) ─────
        logger.info(
            "parse_invoice_text_llm_fallback",
            filename=filename,
            mode=doc.mode,
            prompt_version=settings.invoice_extractor_prompt_version,
        )
        return [await self._text_llm_fallback(doc.text or "", filename=filename)]

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

    # ── LLM text extraction ────────────────────────────────────────────────────

    async def _text_llm_fallback(self, pdf_text: str, filename: str) -> InvoiceParseResult:
        """Extract structured invoice data from text-mode PDF using Claude.

        Prompt loaded dynamically via get_prompt_version() — bump
        settings.invoice_extractor_prompt_version to roll out a new version.
        Falls back to an empty result on API or parse failure.
        """
        prompt_data = get_prompt_version(
            "freight_invoice_extractor", settings.invoice_extractor_prompt_version
        )
        system_prompt: str = prompt_data["SYSTEM_PROMPT"]
        prompt_template: str = prompt_data["PROMPT_TEMPLATE"]
        prompt_version: str = prompt_data["VERSION"]

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        # Cap at 40 000 chars (~10k tokens) — enough for a 30-page scanned invoice
        truncated_text = pdf_text[:40000]
        prompt = prompt_template.format(text=truncated_text)

        logger.info(
            "text_llm_extraction_start",
            filename=filename,
            text_len=len(truncated_text),
            prompt_version=prompt_version,
        )

        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=8192,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=60,
            )
            raw = response.content[0].text if response.content else ""
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data: dict = json.loads(cleaned)

            result = self._compat_to_parse_result(data)
            logger.info(
                "text_llm_extraction_complete",
                filename=filename,
                confidence=result.confidence,
                review_action=result.review_action,
                line_count=len(result.lines),
            )
            return result

        except Exception as exc:
            logger.error(
                "text_llm_extraction_failed",
                filename=filename,
                error=str(exc),
            )
            return InvoiceParseResult(
                header=InvoiceHeader(
                    invoice_number="UNKNOWN",
                    invoice_date=None,
                    carrier_name="Unknown",
                    currency="EUR",
                ),
                lines=[],
                parsing_method="llm",
                confidence=0.0,
                issues=[f"LLM extraction failed: {exc}"],
                review_action="reject",
            )

    # ── conversion helpers ────────────────────────────────────────────────────

    def _compat_to_parse_result(self, compat: dict) -> InvoiceParseResult:
        h = compat["header"]
        confidence = float(compat.get("confidence", 0.0))

        # Determine review_action from confidence thresholds if not supplied by vision pipeline
        raw_action = compat.get("review_action")
        if raw_action is None or not isinstance(raw_action, str):
            if confidence >= _THRESHOLD_AUTO_IMPORT:
                review_action = "auto_import"
            elif confidence >= _THRESHOLD_AUTO_FLAG:
                review_action = "auto_import_flag"
            elif confidence >= _THRESHOLD_HOLD:
                review_action = "hold_for_review"
            else:
                review_action = "reject"
        else:
            review_action = str(raw_action)

        lines = [
            InvoiceLine(
                invoice_number=line.get("invoice_number") or h.get("invoice_number"),
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
            for line in compat.get("lines") or []
        ]

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
            lines=lines,
            parsing_method="llm",
            confidence=confidence,
            issues=compat.get("issues") or [],
            review_action=review_action,
        )
