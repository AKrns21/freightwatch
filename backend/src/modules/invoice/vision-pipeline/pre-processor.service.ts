import { Injectable, Logger } from '@nestjs/common';
import sharp from 'sharp';
import { PdfPageImage } from '../pdf-vision.service';

/**
 * Stage 1 — Pre-processing
 *
 * Takes raw PNG pages rendered by PdfVisionService (≥216 DPI at scale=3) and
 * applies image enhancement via sharp:
 *  - Grayscale conversion  (removes color noise, reduces model distraction)
 *  - Normalise contrast    (auto-stretches histogram → improves legibility of faint text)
 *  - Unsharp masking       (accentuates text edges)
 *  - PNG re-encoding       (lossless, suitable for Vision API)
 *
 * Deskew notes:
 *  Proper document deskew requires angle detection (e.g. Hough-line transform)
 *  which is not built into sharp. Typical carrier invoices from flatbed scanners
 *  are already upright; we apply sharp.rotate() with background:'white' which
 *  auto-corrects Exif orientation flags present on some scanners. A future
 *  upgrade can add an angle-detection pre-pass using jimp or custom convolution.
 */
@Injectable()
export class PreProcessorService {
  private readonly logger = new Logger(PreProcessorService.name);

  /**
   * Process all pages in a document.
   * Returns new PdfPageImage array with enhanced base64 images.
   */
  async processPages(pages: PdfPageImage[]): Promise<PdfPageImage[]> {
    const processed: PdfPageImage[] = [];

    for (const page of pages) {
      try {
        const enhanced = await this.enhancePage(page);
        processed.push(enhanced);
      } catch (error) {
        this.logger.warn({
          event: 'pre_process_page_failed',
          page: page.page_number,
          error: (error as Error).message,
          action: 'using_original',
        });
        // Fallback: keep original image unchanged
        processed.push(page);
      }
    }

    this.logger.log({
      event: 'pre_processing_complete',
      pages_processed: processed.length,
    });

    return processed;
  }

  /** Apply enhancement pipeline to a single page */
  private async enhancePage(page: PdfPageImage): Promise<PdfPageImage> {
    const inputBuffer = Buffer.from(page.image_base64, 'base64');

    const outputBuffer = await sharp(inputBuffer)
      // Exif-based auto-rotation (corrects scanner orientation flags)
      .rotate()
      // Greyscale: removes color noise, reduces file size, focuses model on text
      .grayscale()
      // Normalise: auto-stretches histogram between min/max pixel values
      .normalize()
      // Unsharp mask: radius=1, threshold=0 — accentuates thin text strokes
      .sharpen({ sigma: 1, m1: 0, m2: 3 })
      .png({ compressionLevel: 6 })
      .toBuffer();

    const metadata = await sharp(outputBuffer).metadata();

    const base64 = outputBuffer.toString('base64');
    const sizeKb = Math.round(outputBuffer.length / 1024);

    this.logger.log({
      event: 'pre_process_page_complete',
      page: page.page_number,
      original_size_kb: page.size_kb,
      processed_size_kb: sizeKb,
      width: metadata.width,
      height: metadata.height,
    });

    return {
      page_number: page.page_number,
      image_base64: base64,
      width: metadata.width ?? page.width,
      height: metadata.height ?? page.height,
      size_kb: sizeKb,
    };
  }
}
