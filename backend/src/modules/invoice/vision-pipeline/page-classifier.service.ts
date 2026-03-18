import { Injectable, Logger } from '@nestjs/common';
import Anthropic from '@anthropic-ai/sdk';
import sharp from 'sharp';
import { PdfPageImage } from '../pdf-vision.service';
import { ClassifiedPage, PageType } from './pipeline.types';

/** Thumbnail width (px) sent to Claude for classification — keeps token cost low */
const THUMBNAIL_WIDTH = 512;

/** Claude response per page */
interface PageClassification {
  page: number;
  type: PageType;
  reason: string;
}

/**
 * Stage 2 — Page classification
 *
 * Sends thumbnail-sized versions of all pages in one Claude Haiku call.
 * Each page is classified as:
 *   cover               → title / address sheet
 *   line-item-table     → main line-item grid
 *   surcharge-appendix  → diesel / toll / fee appendix
 *   continuation        → continuation of the previous table
 *
 * Cover pages are NOT skipped immediately; the orchestrator still sends them
 * to the extractor to capture any header fields they contain.
 */
@Injectable()
export class PageClassifierService {
  private readonly logger = new Logger(PageClassifierService.name);
  private readonly anthropic: Anthropic;

  constructor() {
    this.anthropic = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY || '',
    });
  }

  async classifyPages(pages: PdfPageImage[]): Promise<ClassifiedPage[]> {
    if (pages.length === 0) return [];

    this.logger.log({ event: 'page_classification_start', page_count: pages.length });

    // Build thumbnail versions for low-cost classification
    const thumbnails = await Promise.all(pages.map((p) => this.makeThumbnail(p)));

    // Single Haiku call: all thumbnails + classification prompt
    const imageBlocks: Anthropic.ImageBlockParam[] = thumbnails.map((t) => ({
      type: 'image',
      source: { type: 'base64', media_type: 'image/png', data: t },
    }));

    const prompt: Anthropic.TextBlockParam = {
      type: 'text',
      text: this.buildClassificationPrompt(pages.length),
    };

    const response = await this.anthropic.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1024,
      temperature: 0,
      messages: [{ role: 'user', content: [...imageBlocks, prompt] }],
    });

    const raw = response.content
      .filter((b): b is Anthropic.TextBlock => b.type === 'text')
      .map((b) => b.text)
      .join('');

    const classifications = this.parseClassificationResponse(raw, pages.length);

    this.logger.log({
      event: 'page_classification_complete',
      classifications: classifications.map((c) => ({
        page: c.page_number,
        type: c.page_type,
      })),
    });

    return classifications.map((c, i) => ({
      ...c,
      image_base64: pages[i].image_base64, // use full-resolution processed image
    }));
  }

  /** Downscale a page to THUMBNAIL_WIDTH for cheap token usage */
  private async makeThumbnail(page: PdfPageImage): Promise<string> {
    const buf = await sharp(Buffer.from(page.image_base64, 'base64'))
      .resize({ width: THUMBNAIL_WIDTH, withoutEnlargement: true })
      .grayscale()
      .png({ compressionLevel: 9 })
      .toBuffer();
    return buf.toString('base64');
  }

  private buildClassificationPrompt(pageCount: number): string {
    return `You are classifying ${pageCount} page(s) of a scanned German freight carrier invoice (Frachtrechnung).

For each page, assign exactly one type from this list:
  cover               — address page, letter head, cover sheet, or payment stamp page
  line-item-table     — a table of shipment line items (Auftragspositionen / LA-Codes)
  surcharge-appendix  — diesel surcharge table, toll appendix, or fee schedule
  continuation        — continuation of the preceding page's table

Return ONLY a JSON array with one object per page. No explanation, no markdown:
[
  { "page": 0, "type": "cover", "reason": "brief reason" },
  { "page": 1, "type": "line-item-table", "reason": "brief reason" }
]

Rules:
- page is 0-indexed
- If unsure between line-item-table and continuation, prefer line-item-table
- A page with only a grand total row is surcharge-appendix`;
  }

  private parseClassificationResponse(
    raw: string,
    pageCount: number
  ): Array<{ page_number: number; page_type: PageType; width: number; height: number }> {
    const VALID_TYPES: PageType[] = [
      'cover',
      'line-item-table',
      'surcharge-appendix',
      'continuation',
    ];

    try {
      const cleaned = raw.replace(/```json\n?|```/g, '').trim();
      const items = JSON.parse(cleaned) as PageClassification[];

      // Build indexed map (LLM might return out of order)
      const byPage = new Map<number, PageType>();
      for (const item of items) {
        const type: PageType = VALID_TYPES.includes(item.type as PageType)
          ? (item.type as PageType)
          : 'line-item-table';
        byPage.set(item.page, type);
      }

      // Fill missing pages with sensible defaults
      return Array.from({ length: pageCount }, (_, i) => ({
        page_number: i,
        page_type: byPage.get(i) ?? (i === 0 ? 'cover' : 'line-item-table'),
        width: 0,
        height: 0,
      }));
    } catch {
      this.logger.warn({
        event: 'page_classification_parse_error',
        raw_preview: raw.substring(0, 200),
        fallback: 'all_line_item_table',
      });

      // Fallback: treat page 0 as cover, rest as line-item-table
      return Array.from({ length: pageCount }, (_, i) => ({
        page_number: i,
        page_type: i === 0 ? ('cover' as PageType) : ('line-item-table' as PageType),
        width: 0,
        height: 0,
      }));
    }
  }
}
