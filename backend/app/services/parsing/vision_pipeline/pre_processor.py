"""Stage 1 — Pre-processing.

Takes raw PNG pages rendered by DocumentService (≥216 DPI) and applies image
enhancement via Pillow:
  - Exif-based auto-rotation (corrects scanner orientation flags)
  - Grayscale conversion  (removes colour noise, focuses model on text)
  - Auto-contrast / histogram normalisation
  - Unsharp-mask sharpening (accentuates text edges)
  - PNG re-encoding (lossless, suitable for Vision API)

Port of backend_legacy/src/modules/invoice/vision-pipeline/pre-processor.service.ts
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageFilter, ImageOps

from app.services.document_service import PageImage
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PreProcessor:
    """Stage 1: image normalisation for scanned invoice pages."""

    async def process_pages(self, pages: list[PageImage]) -> list[PageImage]:
        """Enhance all pages; fall back to the original on error."""
        processed: list[PageImage] = []

        for page in pages:
            try:
                enhanced = self._enhance_page(page)
                processed.append(enhanced)
            except Exception as exc:
                logger.warning(
                    "pre_process_page_failed",
                    page=page.page_num,
                    error=str(exc),
                    action="using_original",
                )
                processed.append(page)

        logger.info("pre_processing_complete", pages_processed=len(processed))
        return processed

    # ── internal ─────────────────────────────────────────────────────────────

    def _enhance_page(self, page: PageImage) -> PageImage:
        img = Image.open(io.BytesIO(page.image_bytes))

        # Exif-based auto-rotation
        img = ImageOps.exif_transpose(img)

        # Greyscale: removes colour noise, reduces file size
        img = img.convert("L")

        # Auto-contrast: stretches histogram between min/max pixel values
        img = ImageOps.autocontrast(img)

        # Unsharp mask: accentuates thin text strokes (radius≈1, percent=150, threshold=3)
        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False, compress_level=6)
        out_bytes = buf.getvalue()

        size_kb = len(out_bytes) // 1024
        logger.info(
            "pre_process_page_complete",
            page=page.page_num,
            processed_size_kb=size_kb,
            width=img.width,
            height=img.height,
        )

        return PageImage(page_num=page.page_num, image_bytes=out_bytes)


def _page_to_base64(page: PageImage) -> str:
    return base64.b64encode(page.image_bytes).decode()
