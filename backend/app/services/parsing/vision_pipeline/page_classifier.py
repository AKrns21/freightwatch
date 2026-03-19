"""Stage 2 — Page classification.

Sends thumbnail-sized versions of all pages in one Claude Haiku call.
Each page is classified as:
  cover               → title / address sheet
  line-item-table     → main line-item grid
  surcharge-appendix  → diesel / toll / fee appendix
  continuation        → continuation of the previous table

Port of backend_legacy/src/modules/invoice/vision-pipeline/page-classifier.service.ts
"""

from __future__ import annotations

import base64
import io
import json

import anthropic
from PIL import Image

from app.config import settings
from app.services.document_service import PageImage
from app.services.parsing.vision_pipeline.pipeline_types import (
    ClassifiedPage,
    PageType,
    VALID_PAGE_TYPES,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_THUMBNAIL_WIDTH = 512
_HAIKU_MODEL = "claude-haiku-4-5-20251001"


class PageClassifier:
    """Stage 2: classify pages using a single low-cost Claude Haiku call."""

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def classify_pages(self, pages: list[PageImage]) -> list[ClassifiedPage]:
        if not pages:
            return []

        logger.info("page_classification_start", page_count=len(pages))

        thumbnails = [self._make_thumbnail(p) for p in pages]

        image_blocks: list[anthropic.types.ImageBlockParam] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": t},
            }
            for t in thumbnails
        ]

        prompt_block: anthropic.types.TextBlockParam = {
            "type": "text",
            "text": self._build_prompt(len(pages)),
        }

        response = await self._client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=1024,
            temperature=0,
            messages=[{"role": "user", "content": [*image_blocks, prompt_block]}],
        )

        raw = "".join(
            block.text for block in response.content if block.type == "text"
        )

        classifications = self._parse_response(raw, len(pages))

        logger.info(
            "page_classification_complete",
            classifications=[
                {"page": c["page_number"], "type": c["page_type"]}
                for c in classifications
            ],
        )

        return [
            ClassifiedPage(
                page_number=c["page_number"],
                page_type=c["page_type"],
                image_base64=base64.b64encode(pages[i].image_bytes).decode(),
                width=c["width"],
                height=c["height"],
            )
            for i, c in enumerate(classifications)
        ]

    # ── internal ─────────────────────────────────────────────────────────────

    def _make_thumbnail(self, page: PageImage) -> str:
        img = Image.open(io.BytesIO(page.image_bytes)).convert("L")
        if img.width > _THUMBNAIL_WIDTH:
            ratio = _THUMBNAIL_WIDTH / img.width
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=9)
        return base64.b64encode(buf.getvalue()).decode()

    def _build_prompt(self, page_count: int) -> str:
        return (
            f"You are classifying {page_count} page(s) of a scanned German freight "
            "carrier invoice (Frachtrechnung).\n\n"
            "For each page, assign exactly one type from this list:\n"
            "  cover               — address page, letter head, cover sheet, or payment stamp page\n"
            "  line-item-table     — a table of shipment line items (Auftragspositionen / LA-Codes)\n"
            "  surcharge-appendix  — diesel surcharge table, toll appendix, or fee schedule\n"
            "  continuation        — continuation of the preceding page's table\n\n"
            "Return ONLY a JSON array with one object per page. No explanation, no markdown:\n"
            '[\n  { "page": 0, "type": "cover", "reason": "brief reason" },\n'
            '  { "page": 1, "type": "line-item-table", "reason": "brief reason" }\n]\n\n'
            "Rules:\n"
            "- page is 0-indexed\n"
            "- If unsure between line-item-table and continuation, prefer line-item-table\n"
            "- A page with only a grand total row is surcharge-appendix"
        )

    def _parse_response(
        self, raw: str, page_count: int
    ) -> list[dict]:
        try:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            items: list[dict] = json.loads(cleaned)

            by_page: dict[int, PageType] = {}
            for item in items:
                raw_type = item.get("type", "")
                page_type: PageType = (
                    raw_type if raw_type in VALID_PAGE_TYPES else "line-item-table"
                )
                by_page[item["page"]] = page_type

            return [
                {
                    "page_number": i,
                    "page_type": by_page.get(i, "cover" if i == 0 else "line-item-table"),
                    "width": 0,
                    "height": 0,
                }
                for i in range(page_count)
            ]
        except Exception:
            logger.warning(
                "page_classification_parse_error",
                raw_preview=raw[:200],
                fallback="all_line_item_table",
            )
            return [
                {
                    "page_number": i,
                    "page_type": "cover" if i == 0 else "line-item-table",
                    "width": 0,
                    "height": 0,
                }
                for i in range(page_count)
            ]
