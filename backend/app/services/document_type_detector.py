"""Document type detector — 3-step pipeline.

Step 1: Filename heuristics (instant, no I/O).
Step 2: Column pattern matching for structured files (CSV/XLSX).
Step 3: LLM fallback via Claude Haiku (only when steps 1+2 fail on PDFs/images).

DocType values: 'tariff' | 'invoice' | 'shipment_csv' | 'other'
"""

from __future__ import annotations

import json
import re
from typing import Literal

import anthropic
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

DocType = Literal["tariff", "invoice", "shipment_csv", "other"]

# ── keyword lists (lowercase) ──────────────────────────────────────────────

_TARIFF_KEYWORDS: list[str] = [
    "tarif",          # tarif, tariff, tarifblatt
    "entgelt",        # entgelte, entgelttabelle
    "preisliste",
    "preistabelle",
    "ratenkarte",
    "rate_card",
    "ratecard",
    "frachttabelle",
    "frachtsatz",
    "frachtrate",
    "kondition",      # konditionen, sonderkonditionen
]

_INVOICE_KEYWORDS: list[str] = [
    "rechnung",
    "invoice",
    "faktura",
    "gutschrift",
]

# Column name substrings that strongly indicate a shipment list
_SHIPMENT_COLUMNS: list[str] = [
    "dest_zip",
    "origin_zip",
    "weight_kg",
    "shipment",
    "sendung",
    "empfänger",
    "empfaenger",
    "lieferdatum",
    "auftragsnummer",
    "tour",
]

# ── LLM prompt ─────────────────────────────────────────────────────────────

_LLM_PROMPT = """\
Classify this freight/logistics document into exactly one type.

File: {filename}
Content preview:
```
{preview}
```

Types:
- "tariff"       — carrier pricing document: a rate/tariff table, a price announcement \
letter (Ankündigungsschreiben / Preisankündigung) that contains a price table, or any \
document whose main content is freight rates, zone prices, weight-band prices, or \
surcharge percentages — even if the document starts with a cover letter
- "invoice"      — carrier invoice or credit note (invoice number, line items, total)
- "shipment_csv" — list of shipments (origins, destinations, weights, references)
- "other"        — does not fit above or cannot be determined

Respond with a single JSON object only:
{{"type": "tariff" | "invoice" | "shipment_csv" | "other"}}"""


class DocumentTypeDetector:
    """Three-step document type detection pipeline."""

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)
        self._client: anthropic.AsyncAnthropic | None = None

    # ── public API ─────────────────────────────────────────────────────────

    async def detect(
        self,
        filename: str,
        mime_type: str,
        text_preview: str | None = None,
        column_names: list[str] | None = None,
    ) -> DocType:
        """Detect document type using the 3-step pipeline.

        Args:
            filename:     Original upload filename.
            mime_type:    MIME type of the file.
            text_preview: First ~2000 chars of extracted text (for LLM fallback).
            column_names: Header columns from CSV/XLSX (for step 2 matching).

        Returns:
            DocType literal.
        """
        # Step 1: filename heuristics — always runs first
        result = self._by_filename(filename)
        if result is not None:
            logger.info("doc_type_detected", method="filename", filename=filename, doc_type=result)
            return result

        # Step 2: column pattern matching — only for structured files
        if column_names is not None:
            result = self._by_columns(column_names)
            if result is not None:
                logger.info(
                    "doc_type_detected", method="columns", filename=filename, doc_type=result
                )
                return result
            # Structured file with no column match → shipment_csv default
            logger.info(
                "doc_type_detected", method="columns_fallback", filename=filename,
                doc_type="shipment_csv",
            )
            return "shipment_csv"

        # Step 3: LLM fallback — only for ambiguous non-structured files
        if self._is_llm_available() and text_preview is not None:
            result = await self._by_llm(filename, text_preview)
            logger.info("doc_type_detected", method="llm", filename=filename, doc_type=result)
            return result

        logger.info("doc_type_detected", method="fallback", filename=filename, doc_type="other")
        return "other"

    # ── step implementations ───────────────────────────────────────────────

    def _by_filename(self, filename: str) -> DocType | None:
        lower = filename.lower()

        for kw in _TARIFF_KEYWORDS:
            if kw in lower:
                return "tariff"

        for kw in _INVOICE_KEYWORDS:
            if kw in lower:
                return "invoice"

        # "rg" at word boundary (Rechnung abbreviation), e.g. "rg_2024.pdf"
        if re.search(r"(?:^|[^a-z0-9])rg(?:[^a-z0-9]|$)", lower):
            return "invoice"

        return None

    def _by_columns(self, column_names: list[str]) -> DocType | None:
        """Match column headers against known shipment-list patterns."""
        lower_cols = {str(c).lower() for c in column_names}
        matches = sum(
            1 for pat in _SHIPMENT_COLUMNS if any(pat in col for col in lower_cols)
        )
        if matches >= 2:
            return "shipment_csv"
        return None

    async def _by_llm(self, filename: str, text_preview: str) -> DocType:
        """Claude Haiku classification — fast (~1 s), runs only as last resort."""
        try:
            client = self._get_client()
            prompt = _LLM_PROMPT.format(
                filename=filename,
                preview=text_preview[:8000],
            )
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=64,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text if response.content else ""
            # Strip markdown fences if present
            raw = re.sub(r"```json\n?|```", "", raw).strip()
            parsed: dict = json.loads(raw)
            return self._to_doc_type(parsed.get("type", "other"))
        except Exception as exc:
            logger.warning("llm_classification_failed", filename=filename, error=str(exc))
            return "other"

    # ── helpers ────────────────────────────────────────────────────────────

    def _to_doc_type(self, value: str) -> DocType:
        mapping: dict[str, DocType] = {
            "tariff": "tariff",
            "invoice": "invoice",
            "shipment_csv": "shipment_csv",
        }
        return mapping.get(value, "other")

    def _is_llm_available(self) -> bool:
        return bool(settings.anthropic_api_key)

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._client


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_document_type_detector: DocumentTypeDetector | None = None


def get_document_type_detector() -> DocumentTypeDetector:
    """Return the module-level DocumentTypeDetector singleton."""
    global _document_type_detector
    if _document_type_detector is None:
        _document_type_detector = DocumentTypeDetector()
    return _document_type_detector
