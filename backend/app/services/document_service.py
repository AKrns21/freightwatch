"""Document service — unified entry point for all file formats.

Supported formats: PDF (text + Vision OCR), XLSX, XLS, CSV, PNG, JPG/JPEG.

Usage:
    svc = DocumentService()
    result = await svc.process(file_bytes, filename="rechnung.pdf", mime_type="application/pdf")
    print(result.file_hash, result.mode, result.text)
"""

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF
import pandas as pd

from app.services.vision_service import VisionService
from app.utils.error_handler import handle_service_errors
from app.utils.hash import sha256_bytes
from app.utils.logger import get_logger

logger = get_logger(__name__)

ExtractionMode = Literal["text", "vision", "xlsx", "csv", "image"]


@dataclass
class PageImage:
    """A single rendered page image produced during PDF processing."""

    page_num: int
    image_bytes: bytes  # PNG


@dataclass
class DocumentExtractionResult:
    """Unified result of processing any supported document format.

    Attributes:
        file_hash:   SHA-256 hex digest of the raw file bytes.
        mime_type:   MIME type (as provided or auto-detected).
        mode:        How the content was extracted.
        page_count:  Number of pages (1 for single-image/spreadsheet).
        text:        Full extracted text, or None if extraction failed.
        pages:       Rendered page images (only populated for vision pages).
        dataframes:  Pandas DataFrames (populated for CSV/XLSX).
        raw_bytes:   Original file bytes.
    """

    file_hash: str
    mime_type: str
    mode: ExtractionMode
    page_count: int
    text: str | None
    pages: list[PageImage] = field(default_factory=list)
    dataframes: list[pd.DataFrame] = field(default_factory=list)
    raw_bytes: bytes = field(default_factory=bytes)


# MIME type helpers
_PDF_MIMES = {"application/pdf", "application/x-pdf"}
_XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
_CSV_MIMES = {"text/csv", "text/plain"}
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg"}


class DocumentService:
    """Unified document processing service for FreightWatch."""

    def __init__(self) -> None:
        self._vision = VisionService()

    # ── public API ──────────────────────────────────────────────────────────

    @handle_service_errors("document_process")
    async def process(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str | None = None,
    ) -> DocumentExtractionResult:
        """Process a document from raw bytes.

        Args:
            file_bytes: Raw file content.
            filename:   Original filename (used for extension detection).
            mime_type:  MIME type (optional; falls back to extension).

        Returns:
            DocumentExtractionResult with text, dataframes, and metadata.
        """
        file_hash = sha256_bytes(file_bytes)
        ext = Path(filename).suffix.lower()
        resolved_mime = mime_type or self._mime_from_ext(ext)

        logger.info(
            "document_process_start",
            filename=filename,
            ext=ext,
            mime=resolved_mime,
            size_kb=len(file_bytes) // 1024,
            hash=file_hash[:16],
        )

        if ext == ".pdf" or resolved_mime in _PDF_MIMES:
            result = await self._process_pdf(file_bytes, file_hash, resolved_mime)
        elif ext in (".xlsx", ".xls") or resolved_mime in _XLSX_MIMES:
            result = await self._process_xlsx(file_bytes, file_hash, resolved_mime)
        elif ext == ".csv" or resolved_mime in _CSV_MIMES:
            result = await self._process_csv(file_bytes, file_hash, resolved_mime)
        elif ext in (".png", ".jpg", ".jpeg") or resolved_mime in _IMAGE_MIMES:
            result = await self._process_image(file_bytes, file_hash, resolved_mime, ext)
        else:
            logger.warning("unsupported_format", filename=filename, ext=ext)
            result = DocumentExtractionResult(
                file_hash=file_hash,
                mime_type=resolved_mime,
                mode="text",
                page_count=0,
                text=f"[Unsupported file type: {ext}]",
                raw_bytes=file_bytes,
            )

        logger.info(
            "document_process_done",
            filename=filename,
            mode=result.mode,
            page_count=result.page_count,
            text_len=len(result.text) if result.text else 0,
            dataframes=len(result.dataframes),
        )
        return result

    # ── PDF ─────────────────────────────────────────────────────────────────

    @handle_service_errors("pdf_extraction")
    async def _process_pdf(
        self, file_bytes: bytes, file_hash: str, mime_type: str
    ) -> DocumentExtractionResult:
        """Extract text from PDF.

        Strategy per page:
        - Has extractable text → direct text extraction (PyMuPDF)
        - Blank / image-only  → render to PNG, send to Claude Vision
        """
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = len(doc)

        # Classify each page: "text" or "vision"
        page_data: list[tuple[int, str, str]] = []  # (page_num, text, mode)
        for i in range(page_count):
            page = doc[i]
            text = page.get_text()
            mode = "text" if text.strip() else "vision"
            page_data.append((i + 1, text, mode))

        vision_pages = [(pn, doc[pn - 1]) for pn, _, m in page_data if m == "vision"]

        # Fast path: no vision pages
        if not vision_pages:
            doc.close()
            full_text = "\n\n".join(
                f"--- Page {pn} ---\n{text}" for pn, text, _ in page_data
            )
            return DocumentExtractionResult(
                file_hash=file_hash,
                mime_type=mime_type,
                mode="text",
                page_count=page_count,
                text=full_text,
                raw_bytes=file_bytes,
            )

        logger.info(
            "pdf_vision_required",
            total_pages=page_count,
            vision_pages=len(vision_pages),
        )

        # Render vision pages to PNG @ 2x resolution
        rendered: list[tuple[int, bytes]] = []
        page_images_out: list[PageImage] = []
        for pn, fitz_page in vision_pages:
            pix = fitz_page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_bytes = pix.tobytes("png")
            rendered.append((pn, img_bytes))
            page_images_out.append(PageImage(page_num=pn, image_bytes=img_bytes))

        doc.close()

        # Run vision extraction
        vision_results = await self._vision.extract_pages(rendered)

        # Assemble final text in page order
        parts: list[str] = []
        for pn, text, mode in page_data:
            if mode == "text":
                parts.append(f"--- Page {pn} ---\n{text}")
            else:
                vision_text = vision_results.get(pn, f"[Vision failed for page {pn}]")
                parts.append(f"--- Page {pn} (vision) ---\n{vision_text}")

        extraction_mode: ExtractionMode = (
            "vision" if len(vision_pages) == page_count else "text"
        )

        return DocumentExtractionResult(
            file_hash=file_hash,
            mime_type=mime_type,
            mode=extraction_mode,
            page_count=page_count,
            text="\n\n".join(parts),
            pages=page_images_out,
            raw_bytes=file_bytes,
        )

    # ── XLSX ────────────────────────────────────────────────────────────────

    @handle_service_errors("xlsx_extraction")
    async def _process_xlsx(
        self, file_bytes: bytes, file_hash: str, mime_type: str
    ) -> DocumentExtractionResult:
        """Parse XLSX/XLS into DataFrames, one per sheet."""
        buf = io.BytesIO(file_bytes)

        try:
            xl = pd.ExcelFile(buf)
            sheet_names = xl.sheet_names
        except Exception as exc:
            logger.error("xlsx_open_failed", error=str(exc))
            return DocumentExtractionResult(
                file_hash=file_hash,
                mime_type=mime_type,
                mode="xlsx",
                page_count=0,
                text=f"[XLSX open failed: {exc}]",
                raw_bytes=file_bytes,
            )

        dataframes: list[pd.DataFrame] = []
        text_parts: list[str] = []

        for sheet_name in sheet_names:
            try:
                df = pd.read_excel(buf, sheet_name=sheet_name)
                if df.empty:
                    continue
                dataframes.append(df)
                text_parts.append(
                    f"=== Sheet: {sheet_name} "
                    f"({len(df)} rows × {len(df.columns)} cols) ===\n"
                    f"Columns: {', '.join(str(c) for c in df.columns)}\n"
                    f"{df.head(50).to_string(index=False)}"
                )
                if len(df) > 50:
                    text_parts[-1] += f"\n... ({len(df) - 50} more rows)"
            except Exception as exc:
                logger.warning("xlsx_sheet_failed", sheet=sheet_name, error=str(exc))

        return DocumentExtractionResult(
            file_hash=file_hash,
            mime_type=mime_type,
            mode="xlsx",
            page_count=len(sheet_names),
            text="\n\n".join(text_parts) if text_parts else "[No data in XLSX]",
            dataframes=dataframes,
            raw_bytes=file_bytes,
        )

    # ── CSV ─────────────────────────────────────────────────────────────────

    @handle_service_errors("csv_extraction")
    async def _process_csv(
        self, file_bytes: bytes, file_hash: str, mime_type: str
    ) -> DocumentExtractionResult:
        """Parse CSV into a single DataFrame."""
        buf = io.BytesIO(file_bytes)

        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                buf.seek(0)
                df = pd.read_csv(buf, encoding=encoding, on_bad_lines="skip")
                break
            except (UnicodeDecodeError, Exception):
                continue
        else:
            return DocumentExtractionResult(
                file_hash=file_hash,
                mime_type=mime_type,
                mode="csv",
                page_count=0,
                text="[CSV parsing failed: all encodings exhausted]",
                raw_bytes=file_bytes,
            )

        text = (
            f"Columns: {', '.join(str(c) for c in df.columns)}\n"
            f"{df.head(100).to_string(index=False)}"
        )
        if len(df) > 100:
            text += f"\n... ({len(df) - 100} more rows)"

        return DocumentExtractionResult(
            file_hash=file_hash,
            mime_type=mime_type,
            mode="csv",
            page_count=1,
            text=text,
            dataframes=[df],
            raw_bytes=file_bytes,
        )

    # ── Image ────────────────────────────────────────────────────────────────

    @handle_service_errors("image_extraction")
    async def _process_image(
        self, file_bytes: bytes, file_hash: str, mime_type: str, ext: str
    ) -> DocumentExtractionResult:
        """Extract text from a standalone image file via Claude Vision."""
        # Normalise to PNG for vision API
        if ext in (".jpg", ".jpeg"):
            buf = io.BytesIO(file_bytes)
            doc = fitz.open(stream=buf.read(), filetype="jpeg")
            pix = doc[0].get_pixmap()
            png_bytes = pix.tobytes("png")
            doc.close()
        else:
            png_bytes = file_bytes

        text = await self._vision.extract_page(png_bytes, "image_1")
        page_image = PageImage(page_num=1, image_bytes=png_bytes)

        return DocumentExtractionResult(
            file_hash=file_hash,
            mime_type=mime_type,
            mode="image",
            page_count=1,
            text=text,
            pages=[page_image],
            raw_bytes=file_bytes,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _mime_from_ext(ext: str) -> str:
        return {
            ".pdf": "application/pdf",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel",
            ".csv": "text/csv",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(ext, "application/octet-stream")
