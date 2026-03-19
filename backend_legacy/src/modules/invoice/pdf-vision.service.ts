import { Injectable, Logger } from '@nestjs/common';
// mupdf uses top-level await (ESM) and cannot be statically required in CommonJS.
// We load it once lazily on first use via dynamic import().
type MupdfModule = typeof import('mupdf');
let _mupdf: MupdfModule | null = null;
async function getMupdf(): Promise<MupdfModule> {
  if (!_mupdf) _mupdf = await import('mupdf');
  return _mupdf;
}

export interface PdfPageImage {
  page_number: number;
  image_base64: string;
  width: number;
  height: number;
  size_kb: number;
}

export interface PdfExtractionResult {
  mode: 'text' | 'vision';
  /** Populated when mode === 'text' */
  text?: string;
  /** Populated when mode === 'vision' */
  pages?: PdfPageImage[];
  page_count: number;
}

/** Minimum average characters per page to consider a PDF text-based (not a scan) */
const MIN_AVG_CHARS_PER_PAGE = 50;

/** Render scale factor – 3x → ~216 DPI (72 DPI base × 3), meets the ≥200 DPI requirement */
const RENDER_SCALE = 3;

/**
 * PdfVisionService
 *
 * Detects whether a PDF is text-based or a scan and returns the appropriate
 * extraction result:
 *
 *  - text mode: selectable text extracted directly from the PDF
 *  - vision mode: each page rendered as a base64 PNG for Vision-LLM OCR
 *
 * Uses the `mupdf` npm package (pure WebAssembly port of MuPDF –
 * no system dependencies like ImageMagick or Ghostscript required).
 */
@Injectable()
export class PdfVisionService {
  private readonly logger = new Logger(PdfVisionService.name);

  /**
   * Extract content from a PDF buffer.
   *
   * Strategy (mirrors Oxytec DocumentService._extract_pdf):
   * 1. Try text extraction with mupdf – fast, free
   * 2. If avg chars/page < threshold → scan detected → render pages as PNG
   */
  async extractFromBuffer(buffer: Buffer): Promise<PdfExtractionResult> {
    const mupdf = await getMupdf();
    const doc = mupdf.Document.openDocument(
      new Uint8Array(buffer),
      'application/pdf',
    );

    const pageCount = doc.countPages();

    // Phase 1: text extraction pass
    const textParts: string[] = [];
    let totalChars = 0;

    for (let i = 0; i < pageCount; i++) {
      const page = doc.loadPage(i);
      const text = page.toStructuredText('preserve-whitespace').asText();
      textParts.push(text);
      totalChars += text.trim().length;
    }

    const avgCharsPerPage = pageCount > 0 ? totalChars / pageCount : 0;
    const emptyPageCount = textParts.filter((t) => t.trim().length < 20).length;
    const emptyFraction = pageCount > 0 ? emptyPageCount / pageCount : 1;
    const isTextMode = avgCharsPerPage >= MIN_AVG_CHARS_PER_PAGE && emptyFraction < 0.5;

    if (isTextMode) {
      this.logger.log({
        event: 'pdf_text_mode',
        page_count: pageCount,
        total_chars: totalChars,
        avg_chars_per_page: Math.round(avgCharsPerPage),
      });

      return {
        mode: 'text',
        text: textParts.join('\n\n--- PAGE BREAK ---\n\n'),
        page_count: pageCount,
      };
    }

    // Phase 2: scan or mixed PDF detected – render all pages to PNG
    this.logger.log({
      event: 'pdf_scan_detected',
      page_count: pageCount,
      avg_chars_per_page: Math.round(avgCharsPerPage),
      empty_fraction: Math.round(emptyFraction * 100),
      strategy: 'vision_ocr',
    });

    const pages: PdfPageImage[] = [];

    for (let i = 0; i < pageCount; i++) {
      const page = doc.loadPage(i);

      // Scale matrix as flat array [sx, shx, shy, sy, tx, ty]
      const matrix: [number, number, number, number, number, number] = [
        RENDER_SCALE, 0, 0, RENDER_SCALE, 0, 0,
      ];

      const pixmap = page.toPixmap(
        matrix,
        mupdf.ColorSpace.DeviceRGB,
        false, // no alpha
        true,  // anti-alias
      );

      const pngBytes = pixmap.asPNG();
      const sizeKb = Math.round(pngBytes.length / 1024);
      const base64 = Buffer.from(pngBytes).toString('base64');

      pages.push({
        page_number: i,
        image_base64: base64,
        width: pixmap.getWidth(),
        height: pixmap.getHeight(),
        size_kb: sizeKb,
      });

      this.logger.log({
        event: 'pdf_page_rendered',
        page: i + 1,
        total_pages: pageCount,
        width: pixmap.getWidth(),
        height: pixmap.getHeight(),
        size_kb: sizeKb,
      });
    }

    return {
      mode: 'vision',
      pages,
      page_count: pageCount,
    };
  }
}
