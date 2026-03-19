"""Claude Vision OCR service — single-model, freight-domain extraction.

FreightWatch uses Claude only (no dual-model). Handles scanned PDFs and images.
"""

import asyncio
import base64
import time

import anthropic

from app.config import settings
from app.utils.error_handler import handle_service_errors
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Prompt focused on freight document types (tariffs, invoices, shipment lists)
_VISION_PROMPT = """\
You are an OCR assistant for a freight logistics platform.
Extract ALL text content from this document page exactly as it appears.

The document is one of:
- Carrier tariff sheet (Tarifblatt): zones, weight bands, rates, surcharges (Nebenkosten)
- Freight invoice (Frachtrechnung): header, line items with weights, routes, amounts
- Shipment list (Sendungsliste): CSV-like rows with origins, destinations, weights, costs

Output the full extracted text preserving the table structure as closely as possible.
Use | to separate table columns. Use blank lines to separate sections.
Preserve all numbers, postal codes, and currency amounts exactly as shown.
Do NOT summarise or interpret — transcribe everything visible."""


class VisionService:
    """Single-model Claude vision extraction for freight documents."""

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @handle_service_errors("vision_extract_page")
    async def extract_page(self, image_bytes: bytes, page_id: str) -> str:
        """Extract text from a single page image using Claude Vision.

        Args:
            image_bytes: PNG image data.
            page_id: Log identifier (e.g. "page_3").

        Returns:
            Extracted text, or an error placeholder string on failure.
        """
        start = time.time()
        image_b64 = base64.standard_b64encode(image_bytes).decode()

        logger.info(
            "vision_extract_start",
            page=page_id,
            image_kb=len(image_bytes) // 1024,
            model=settings.vision_model,
        )

        response = await asyncio.wait_for(
            self._client.messages.create(
                model=settings.vision_model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": _VISION_PROMPT},
                        ],
                    }
                ],
            ),
            timeout=120,
        )

        text = response.content[0].text if response.content else ""
        elapsed = round(time.time() - start, 2)

        if not text.strip():
            logger.warning("vision_extract_empty", page=page_id, elapsed_s=elapsed)
            return f"[Vision returned no content for {page_id}]"

        logger.info(
            "vision_extract_done",
            page=page_id,
            chars=len(text),
            elapsed_s=elapsed,
        )
        return text

    async def extract_pages(
        self, page_images: list[tuple[int, bytes]]
    ) -> dict[int, str]:
        """Extract text from multiple pages concurrently (max 5 in parallel).

        Args:
            page_images: List of (page_num, png_bytes).

        Returns:
            Dict mapping page_num → extracted text.
        """
        sem = asyncio.Semaphore(5)

        async def _extract_one(page_num: int, img: bytes) -> tuple[int, str]:
            async with sem:
                text = await self.extract_page(img, f"page_{page_num}")
                return page_num, text

        results = await asyncio.gather(
            *[_extract_one(pn, img) for pn, img in page_images],
            return_exceptions=True,
        )

        out: dict[int, str] = {}
        for pn, item in zip((pn for pn, _ in page_images), results):
            if isinstance(item, Exception):
                logger.error("vision_page_failed", page=pn, error=str(item))
                out[pn] = f"[Vision extraction failed for page_{pn}: {item}]"
            else:
                _, text = item  # type: ignore[misc]
                out[pn] = text
        return out
