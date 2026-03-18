import { Injectable, Logger } from '@nestjs/common';
import Anthropic from '@anthropic-ai/sdk';
import {
  ClassifiedPage,
  ExtractedHeader,
  ExtractedLine,
  AnnotatedField,
  FieldSource,
  PageExtractionResult,
} from './pipeline.types';

/** Carrier-specific system prompt hints (keyed by carrier_id or canonical name) */
const CARRIER_HINTS: Record<string, string> = {
  dhl: 'DHL uses LA-Codes (200=Standard, 201=Express). Billing unit columns: "Menge", "Preis", "Gesamt EUR".',
  dpd: 'DPD invoices list parcel reference in "Barcode" column. Weight in "Gewicht" column.',
  ups: 'UPS invoices list tracking number in "Sendungsnr." column.',
  fedex: 'FedEx invoices use "Tracking-ID" and "Serviceart" columns.',
  hermes: 'Hermes invoices use "Auftragsnummer" for reference.',
};

/**
 * Stage 3 — Structured extraction (per page)
 *
 * For each non-continuation page sends a Claude Sonnet message with:
 *  - the processed page image
 *  - page type context (line-item-table | surcharge-appendix | cover)
 *  - optional carrier-specific system prompt hint
 *
 * Returns `PageExtractionResult` per page with field-level source annotations
 * (`direct_ocr` | `llm_inferred` | `missing`) so Stage 5 can compute confidence.
 *
 * Tables are extracted as array-of-rows (not flat lists).
 */
@Injectable()
export class StructuredExtractorService {
  private readonly logger = new Logger(StructuredExtractorService.name);
  private readonly anthropic: Anthropic;

  constructor() {
    this.anthropic = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY || '',
    });
  }

  /**
   * Extract structured data from all classified pages.
   * Pages are processed concurrently with a concurrency cap of 3.
   */
  async extractPages(
    pages: ClassifiedPage[],
    carrierId?: string
  ): Promise<PageExtractionResult[]> {
    this.logger.log({
      event: 'structured_extraction_start',
      page_count: pages.length,
      carrier_id: carrierId,
    });

    const results: PageExtractionResult[] = [];

    // Process in batches of 3 to stay within API concurrency limits
    for (let i = 0; i < pages.length; i += 3) {
      const batch = pages.slice(i, i + 3);
      const batchResults = await Promise.all(
        batch.map((page) => this.extractPage(page, carrierId))
      );
      results.push(...batchResults);
    }

    this.logger.log({
      event: 'structured_extraction_complete',
      pages_extracted: results.length,
      total_lines: results.reduce((s, r) => s + r.lines.length, 0),
    });

    return results;
  }

  private async extractPage(
    page: ClassifiedPage,
    carrierId?: string
  ): Promise<PageExtractionResult> {
    const systemPrompt = this.buildSystemPrompt(page.page_type, carrierId);
    const userPrompt = this.buildUserPrompt(page.page_type);

    try {
      const response = await this.anthropic.messages.create({
        model: 'claude-sonnet-4-6',
        max_tokens: 8192,
        system: systemPrompt,
        messages: [
          {
            role: 'user',
            content: [
              {
                type: 'image',
                source: {
                  type: 'base64',
                  media_type: 'image/png',
                  data: page.image_base64,
                },
              },
              { type: 'text', text: userPrompt },
            ],
          },
        ],
      });

      const raw = response.content
        .filter((b): b is Anthropic.TextBlock => b.type === 'text')
        .map((b) => b.text)
        .join('');

      return this.parsePageResponse(raw, page);
    } catch (error) {
      this.logger.error({
        event: 'structured_extraction_page_error',
        page: page.page_number,
        error: (error as Error).message,
      });

      return {
        page_number: page.page_number,
        page_type: page.page_type,
        lines: [],
        raw_issues: [`Page ${page.page_number} extraction failed: ${(error as Error).message}`],
      };
    }
  }

  // ─── Prompt builders ──────────────────────────────────────────────────────

  private buildSystemPrompt(pageType: ClassifiedPage['page_type'], carrierId?: string): string {
    const carrierKey = carrierId?.toLowerCase().replace(/[^a-z]/g, '') ?? '';
    const carrierHint = CARRIER_HINTS[carrierKey] ?? '';

    return `You are a precise data extraction engine for German freight carrier invoices.
Your output MUST be a single valid JSON object — no markdown, no explanation, no code fences.

Every field value must be accompanied by a source annotation:
  "direct_ocr"   — value is clearly printed and directly readable
  "llm_inferred" — value was derived from context or partial text
  "missing"      — field is not present on this page

Field format: { "value": <extracted_value_or_null>, "src": "<source>" }

${carrierHint ? `Carrier hint: ${carrierHint}` : ''}
Page type: ${pageType}`;
  }

  private buildUserPrompt(pageType: ClassifiedPage['page_type']): string {
    if (pageType === 'cover') {
      return `Extract invoice header information from this cover page.
Return JSON:
{
  "header": {
    "invoice_number":     { "value": "string|null", "src": "direct_ocr|llm_inferred|missing" },
    "invoice_date":       { "value": "YYYY-MM-DD|null", "src": "..." },
    "carrier_name":       { "value": "string|null", "src": "..." },
    "customer_name":      { "value": "string|null", "src": "..." },
    "customer_number":    { "value": "string|null", "src": "..." },
    "total_net_amount":   { "value": number|null, "src": "..." },
    "total_gross_amount": { "value": number|null, "src": "..." },
    "currency":           { "value": "EUR|CHF|USD|null", "src": "..." }
  },
  "lines": [],
  "issues": []
}`;
    }

    if (pageType === 'surcharge-appendix') {
      return `Extract surcharge/appendix rows from this page as line items.
Return JSON:
{
  "header": null,
  "lines": [
    {
      "shipment_date":      { "value": "YYYY-MM-DD|null", "src": "direct_ocr|llm_inferred|missing" },
      "shipment_reference": { "value": "string|null", "src": "..." },
      "tour":               { "value": "string|null", "src": "..." },
      "origin_zip":         { "value": "5-digit PLZ|null", "src": "..." },
      "origin_country":     { "value": "2-letter ISO|null", "src": "..." },
      "dest_zip":           { "value": "5-digit PLZ|null", "src": "..." },
      "dest_country":       { "value": "2-letter ISO|null", "src": "..." },
      "weight_kg":          { "value": number|null, "src": "..." },
      "unit_price":         { "value": number|null, "src": "..." },
      "line_total":         { "value": number|null, "src": "..." },
      "billing_type":       { "value": "LA code|null", "src": "..." }
    }
  ],
  "issues": []
}`;
    }

    // line-item-table or continuation
    return `Extract all shipment line items from this page as an array of rows.
Also extract any invoice header fields visible on this page.

Return JSON:
{
  "header": {
    "invoice_number":     { "value": "string|null", "src": "direct_ocr|llm_inferred|missing" },
    "invoice_date":       { "value": "YYYY-MM-DD|null", "src": "..." },
    "carrier_name":       { "value": "string|null", "src": "..." },
    "customer_name":      { "value": "string|null", "src": "..." },
    "customer_number":    { "value": "string|null", "src": "..." },
    "total_net_amount":   { "value": number|null, "src": "..." },
    "total_gross_amount": { "value": number|null, "src": "..." },
    "currency":           { "value": "EUR|CHF|USD|null", "src": "..." }
  },
  "lines": [
    {
      "shipment_date":      { "value": "YYYY-MM-DD|null", "src": "direct_ocr|llm_inferred|missing" },
      "shipment_reference": { "value": "string|null", "src": "..." },
      "tour":               { "value": "string|null", "src": "..." },
      "origin_zip":         { "value": "5-digit PLZ extracted from full address|null", "src": "..." },
      "origin_country":     { "value": "DE", "src": "..." },
      "dest_zip":           { "value": "5-digit PLZ|null", "src": "..." },
      "dest_country":       { "value": "DE", "src": "..." },
      "weight_kg":          { "value": number|null, "src": "..." },
      "unit_price":         { "value": number|null, "src": "..." },
      "line_total":         { "value": number|null, "src": "..." },
      "billing_type":       { "value": "LA-Code e.g. 200|null", "src": "..." }
    }
  ],
  "issues": ["any data quality problems found"]
}

Rules:
- Convert German dates (dd.mm.yy / dd.mm.yyyy) → YYYY-MM-DD
- Remove thousand separators; use period as decimal separator
- Extract PLZ from addresses like "D-42551 Velbert" → "42551"
- One object per shipment row; skip VAT summary rows and grand-total rows
- If a field is illegible or absent: value=null, src="missing"`;
  }

  // ─── Response parser ──────────────────────────────────────────────────────

  private parsePageResponse(raw: string, page: ClassifiedPage): PageExtractionResult {
    try {
      const cleaned = raw.replace(/```json\n?|```/g, '').trim();
      const data = JSON.parse(cleaned) as {
        header?: Record<string, { value: unknown; src: string }> | null;
        lines: Array<Record<string, { value: unknown; src: string }>>;
        issues?: string[];
      };

      const header = data.header ? this.parseHeader(data.header) : undefined;
      const lines = (data.lines ?? []).map((row) => this.parseLine(row));

      return {
        page_number: page.page_number,
        page_type: page.page_type,
        header,
        lines,
        raw_issues: data.issues ?? [],
      };
    } catch {
      this.logger.warn({
        event: 'structured_extraction_parse_error',
        page: page.page_number,
        raw_preview: raw.substring(0, 300),
      });

      return {
        page_number: page.page_number,
        page_type: page.page_type,
        lines: [],
        raw_issues: [`JSON parse error on page ${page.page_number}`],
      };
    }
  }

  private parseHeader(
    raw: Record<string, { value: unknown; src: string }>
  ): ExtractedHeader {
    const af = <T>(key: string): AnnotatedField<T> => ({
      value: (raw[key]?.value ?? null) as T,
      src: this.toFieldSource(raw[key]?.src),
    });

    return {
      invoice_number:     af<string | null>('invoice_number'),
      invoice_date:       af<string | null>('invoice_date'),
      carrier_name:       af<string | null>('carrier_name'),
      customer_name:      af<string | null>('customer_name'),
      customer_number:    af<string | null>('customer_number'),
      total_net_amount:   af<number | null>('total_net_amount'),
      total_gross_amount: af<number | null>('total_gross_amount'),
      currency:           af<string | null>('currency'),
    };
  }

  private parseLine(
    raw: Record<string, { value: unknown; src: string }>
  ): ExtractedLine {
    const af = <T>(key: string): AnnotatedField<T> => ({
      value: (raw[key]?.value ?? null) as T,
      src: this.toFieldSource(raw[key]?.src),
    });

    return {
      shipment_date:      af<string | null>('shipment_date'),
      shipment_reference: af<string | null>('shipment_reference'),
      tour:               af<string | null>('tour'),
      origin_zip:         af<string | null>('origin_zip'),
      origin_country:     af<string | null>('origin_country'),
      dest_zip:           af<string | null>('dest_zip'),
      dest_country:       af<string | null>('dest_country'),
      weight_kg:          af<number | null>('weight_kg'),
      unit_price:         af<number | null>('unit_price'),
      line_total:         af<number | null>('line_total'),
      billing_type:       af<string | null>('billing_type'),
    };
  }

  private toFieldSource(raw: string | undefined): FieldSource {
    if (raw === 'direct_ocr' || raw === 'llm_inferred' || raw === 'missing') return raw;
    return 'llm_inferred';
  }
}
