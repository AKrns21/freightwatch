"""Stage 3 — Structured extraction (per page).

For each non-continuation page sends a Claude Sonnet message with:
  - the processed page image
  - page-type context (line-item-table | surcharge-appendix | cover)
  - optional carrier-specific system prompt hint

Returns PageExtractionResult per page with field-level source annotations
(direct_ocr | llm_inferred | missing) so Stage 5 can compute confidence.

Port of backend_legacy/src/modules/invoice/vision-pipeline/structured-extractor.service.ts
"""

from __future__ import annotations

import asyncio
import json

import anthropic

from app.config import settings
from app.services.parsing.vision_pipeline.pipeline_types import (
    AnnotatedField,
    ClassifiedPage,
    ExtractedHeader,
    ExtractedLine,
    FieldSource,
    PageExtractionResult,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SONNET_MODEL = "claude-sonnet-4-6"
_CONCURRENCY = 3

_CARRIER_HINTS: dict[str, str] = {
    "dhl": 'DHL uses LA-Codes (200=Standard, 201=Express). Billing unit columns: "Menge", "Preis", "Gesamt EUR".',
    "dpd": 'DPD invoices list parcel reference in "Barcode" column. Weight in "Gewicht" column.',
    "ups": 'UPS invoices list tracking number in "Sendungsnr." column.',
    "fedex": 'FedEx invoices use "Tracking-ID" and "Serviceart" columns.',
    "hermes": 'Hermes invoices use "Auftragsnummer" for reference.',
}


class StructuredExtractor:
    """Stage 3: per-page structured extraction with field-src annotations."""

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def extract_pages(
        self, pages: list[ClassifiedPage], carrier_id: str | None = None
    ) -> list[PageExtractionResult]:
        logger.info(
            "structured_extraction_start",
            page_count=len(pages),
            carrier_id=carrier_id,
        )

        results: list[PageExtractionResult] = []
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _bounded(page: ClassifiedPage) -> PageExtractionResult:
            async with sem:
                return await self._extract_page(page, carrier_id)

        results = await asyncio.gather(*[_bounded(p) for p in pages])

        logger.info(
            "structured_extraction_complete",
            pages_extracted=len(results),
            total_lines=sum(len(r.lines) for r in results),
        )
        return list(results)

    # ── internal ─────────────────────────────────────────────────────────────

    async def _extract_page(
        self, page: ClassifiedPage, carrier_id: str | None
    ) -> PageExtractionResult:
        system_prompt = self._build_system_prompt(page.page_type, carrier_id)
        user_prompt = self._build_user_prompt(page.page_type)

        try:
            response = await self._client.messages.create(
                model=_SONNET_MODEL,
                max_tokens=8192,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": page.image_base64,
                                },
                            },
                            {"type": "text", "text": user_prompt},
                        ],
                    }
                ],
            )

            raw = "".join(
                block.text for block in response.content if block.type == "text"
            )
            return self._parse_response(raw, page)

        except Exception as exc:
            logger.error(
                "structured_extraction_page_error",
                page=page.page_number,
                error=str(exc),
            )
            return PageExtractionResult(
                page_number=page.page_number,
                page_type=page.page_type,
                lines=[],
                raw_issues=[
                    f"Page {page.page_number} extraction failed: {exc}"
                ],
            )

    def _build_system_prompt(
        self, page_type: str, carrier_id: str | None
    ) -> str:
        carrier_key = "".join(c for c in (carrier_id or "").lower() if c.isalpha())
        carrier_hint = _CARRIER_HINTS.get(carrier_key, "")

        return (
            "You are a precise data extraction engine for German freight carrier invoices.\n"
            "Your output MUST be a single valid JSON object — no markdown, no explanation, no code fences.\n\n"
            "Every field value must be accompanied by a source annotation:\n"
            '  "direct_ocr"   — value is clearly printed and directly readable\n'
            '  "llm_inferred" — value was derived from context or partial text\n'
            '  "missing"      — field is not present on this page\n\n'
            'Field format: { "value": <extracted_value_or_null>, "src": "<source>" }\n\n'
            + (f"Carrier hint: {carrier_hint}\n" if carrier_hint else "")
            + f"Page type: {page_type}"
        )

    def _build_user_prompt(self, page_type: str) -> str:
        if page_type == "cover":
            return (
                "Extract invoice header information from this cover page.\n"
                "Return JSON:\n"
                "{\n"
                '  "header": {\n'
                '    "invoice_number":     { "value": "string|null", "src": "direct_ocr|llm_inferred|missing" },\n'
                '    "invoice_date":       { "value": "YYYY-MM-DD|null", "src": "..." },\n'
                '    "carrier_name":       { "value": "string|null", "src": "..." },\n'
                '    "customer_name":      { "value": "string|null", "src": "..." },\n'
                '    "customer_number":    { "value": "string|null", "src": "..." },\n'
                '    "total_net_amount":   { "value": "number|null", "src": "..." },\n'
                '    "total_gross_amount": { "value": "number|null", "src": "..." },\n'
                '    "currency":           { "value": "EUR|CHF|USD|null", "src": "..." }\n'
                "  },\n"
                '  "lines": [],\n'
                '  "issues": []\n'
                "}"
            )

        if page_type == "surcharge-appendix":
            return (
                "Extract surcharge/appendix rows from this page as line items.\n"
                "Return JSON:\n"
                "{\n"
                '  "header": null,\n'
                '  "lines": [\n'
                "    {\n"
                '      "shipment_date":      { "value": "YYYY-MM-DD|null", "src": "direct_ocr|llm_inferred|missing" },\n'
                '      "shipment_reference": { "value": "string|null", "src": "..." },\n'
                '      "tour":               { "value": "string|null", "src": "..." },\n'
                '      "origin_zip":         { "value": "5-digit PLZ|null", "src": "..." },\n'
                '      "origin_country":     { "value": "2-letter ISO|null", "src": "..." },\n'
                '      "dest_zip":           { "value": "5-digit PLZ|null", "src": "..." },\n'
                '      "dest_country":       { "value": "2-letter ISO|null", "src": "..." },\n'
                '      "weight_kg":          { "value": "number|null", "src": "..." },\n'
                '      "unit_price":         { "value": "number|null", "src": "..." },\n'
                '      "line_total":         { "value": "number|null", "src": "..." },\n'
                '      "billing_type":       { "value": "LA code|null", "src": "..." }\n'
                "    }\n"
                "  ],\n"
                '  "issues": []\n'
                "}"
            )

        # line-item-table or continuation
        return (
            "Extract all shipment line items from this page as an array of rows.\n"
            "Also extract any invoice header fields visible on this page.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "header": {\n'
            '    "invoice_number":     { "value": "string|null", "src": "direct_ocr|llm_inferred|missing" },\n'
            '    "invoice_date":       { "value": "YYYY-MM-DD|null", "src": "..." },\n'
            '    "carrier_name":       { "value": "string|null", "src": "..." },\n'
            '    "customer_name":      { "value": "string|null", "src": "..." },\n'
            '    "customer_number":    { "value": "string|null", "src": "..." },\n'
            '    "total_net_amount":   { "value": "number|null", "src": "..." },\n'
            '    "total_gross_amount": { "value": "number|null", "src": "..." },\n'
            '    "currency":           { "value": "EUR|CHF|USD|null", "src": "..." }\n'
            "  },\n"
            '  "lines": [\n'
            "    {\n"
            '      "shipment_date":      { "value": "YYYY-MM-DD|null", "src": "direct_ocr|llm_inferred|missing" },\n'
            '      "shipment_reference": { "value": "string|null", "src": "..." },\n'
            '      "tour":               { "value": "string|null", "src": "..." },\n'
            '      "origin_zip":         { "value": "5-digit PLZ extracted from full address|null", "src": "..." },\n'
            '      "origin_country":     { "value": "DE", "src": "..." },\n'
            '      "dest_zip":           { "value": "5-digit PLZ|null", "src": "..." },\n'
            '      "dest_country":       { "value": "DE", "src": "..." },\n'
            '      "weight_kg":          { "value": "number|null", "src": "..." },\n'
            '      "unit_price":         { "value": "number|null", "src": "..." },\n'
            '      "line_total":         { "value": "number|null", "src": "..." },\n'
            '      "billing_type":       { "value": "LA-Code e.g. 200|null", "src": "..." }\n'
            "    }\n"
            "  ],\n"
            '  "issues": ["any data quality problems found"]\n'
            "}\n\n"
            "Rules:\n"
            "- Convert German dates (dd.mm.yy / dd.mm.yyyy) → YYYY-MM-DD\n"
            "- Remove thousand separators; use period as decimal separator\n"
            '- Extract PLZ from addresses like "D-42551 Velbert" → "42551"\n'
            "- One object per shipment row; skip VAT summary rows and grand-total rows\n"
            '- If a field is illegible or absent: value=null, src="missing"'
        )

    # ── response parsers ──────────────────────────────────────────────────────

    def _parse_response(self, raw: str, page: ClassifiedPage) -> PageExtractionResult:
        try:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data: dict = json.loads(cleaned)

            header_raw = data.get("header")
            header = self._parse_header(header_raw) if header_raw else None
            lines = [self._parse_line(row) for row in (data.get("lines") or [])]

            return PageExtractionResult(
                page_number=page.page_number,
                page_type=page.page_type,
                header=header,
                lines=lines,
                raw_issues=data.get("issues") or [],
            )
        except Exception:
            logger.warning(
                "structured_extraction_parse_error",
                page=page.page_number,
                raw_preview=raw[:300],
            )
            return PageExtractionResult(
                page_number=page.page_number,
                page_type=page.page_type,
                lines=[],
                raw_issues=[f"JSON parse error on page {page.page_number}"],
            )

    def _af(self, raw: dict, key: str) -> AnnotatedField:
        entry = raw.get(key) or {}
        return AnnotatedField(
            value=entry.get("value"),
            src=self._to_field_source(entry.get("src")),
        )

    def _to_field_source(self, raw: str | None) -> FieldSource:
        if raw in ("direct_ocr", "llm_inferred", "missing"):
            return raw  # type: ignore[return-value]
        return "llm_inferred"

    def _parse_header(self, raw: dict) -> ExtractedHeader:
        af = lambda key: self._af(raw, key)
        return ExtractedHeader(
            invoice_number=af("invoice_number"),
            invoice_date=af("invoice_date"),
            carrier_name=af("carrier_name"),
            customer_name=af("customer_name"),
            customer_number=af("customer_number"),
            total_net_amount=af("total_net_amount"),
            total_gross_amount=af("total_gross_amount"),
            currency=af("currency"),
        )

    def _parse_line(self, raw: dict) -> ExtractedLine:
        af = lambda key: self._af(raw, key)
        return ExtractedLine(
            shipment_date=af("shipment_date"),
            shipment_reference=af("shipment_reference"),
            tour=af("tour"),
            origin_zip=af("origin_zip"),
            origin_country=af("origin_country"),
            dest_zip=af("dest_zip"),
            dest_country=af("dest_country"),
            weight_kg=af("weight_kg"),
            unit_price=af("unit_price"),
            line_total=af("line_total"),
            billing_type=af("billing_type"),
        )
