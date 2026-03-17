import { Injectable, Logger } from '@nestjs/common';
import Anthropic from '@anthropic-ai/sdk';
import { DocType } from './entities/upload.entity';

/**
 * DocumentClassifierService — Issue #22
 *
 * Classifies uploaded documents using a 4-step priority pipeline:
 *  1. File extension  — .xlsx/.csv → structured (skip LLM)
 *  2. Filename heuristics — keyword matching for tariff/invoice
 *  3. LLM classification — for ambiguous PDFs/images
 *  4. User override   — handled at call site (sourceType param)
 */
@Injectable()
export class DocumentClassifierService {
  private readonly logger = new Logger(DocumentClassifierService.name);
  private readonly anthropic: Anthropic;

  // Filename keywords → doc_type (case-insensitive, German + English)
  private readonly TARIFF_KEYWORDS = [
    'tarif',    // Tarif, Tariff
    'entgelt',  // Entgelte, Entgelttabelle
    'preisliste',
    'preistabelle',
    'ratenkarte',
    'rate_card',
    'ratecard',
    'frachttabelle',
    'frachtsatz',
  ];

  private readonly INVOICE_KEYWORDS = [
    'rechnung',
    'invoice',
    'faktura',
    'gutschrift',
    // 'rg' handled separately with word-boundary regex
  ];

  constructor() {
    this.anthropic = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY || 'dummy-key',
    });
  }

  /**
   * Full classification pipeline.
   * Returns detected DocType.
   *
   * @param filename   Original filename
   * @param mimeType   MIME type of the file
   * @param content    Optional text content (used for LLM fallback on PDFs)
   */
  async classify(filename: string, mimeType: string, content?: string): Promise<DocType> {
    // Step 1: Structured-file extensions never need LLM
    const isStructured = this.isStructuredFile(filename, mimeType);

    // Step 2: Filename heuristics (applies to all file types)
    const heuristicResult = this.classifyByFilename(filename);
    if (heuristicResult !== null) {
      this.logger.log({
        event: 'doc_type_detected',
        method: 'filename_heuristic',
        filename,
        doc_type: heuristicResult,
      });
      return heuristicResult;
    }

    // Step 3: LLM fallback — only for non-structured (PDF/image) files
    if (!isStructured && this.isLlmAvailable()) {
      try {
        const llmResult = await this.classifyByLlm(content ?? '', filename, mimeType);
        this.logger.log({
          event: 'doc_type_detected',
          method: 'llm',
          filename,
          doc_type: llmResult,
        });
        return llmResult;
      } catch (error) {
        this.logger.warn({
          event: 'llm_classification_failed',
          filename,
          error: (error as Error).message,
        });
      }
    }

    // Default for structured files without heuristic match → shipment_csv
    // Default for everything else → other
    const fallback = isStructured ? DocType.SHIPMENT_CSV : DocType.OTHER;
    this.logger.log({
      event: 'doc_type_detected',
      method: 'fallback',
      filename,
      doc_type: fallback,
    });
    return fallback;
  }

  /**
   * Step 1: Check if this is a structured file (CSV/Excel).
   * These never need LLM vision analysis.
   */
  isStructuredFile(filename: string, mimeType: string): boolean {
    const ext = this.getExtension(filename);
    if (ext === '.csv' || ext === '.xls' || ext === '.xlsx') return true;

    return (
      mimeType === 'text/csv' ||
      mimeType === 'application/vnd.ms-excel' ||
      mimeType === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    );
  }

  /**
   * Step 2: Classify by filename keywords.
   * Returns null if no keyword matches.
   */
  classifyByFilename(filename: string): DocType | null {
    const lower = filename.toLowerCase();

    // Check tariff keywords
    for (const kw of this.TARIFF_KEYWORDS) {
      if (lower.includes(kw)) return DocType.TARIFF;
    }

    // Check invoice keywords
    // Special case: bare "rg" must appear at word boundary to avoid false positives
    if (this.hasInvoiceKeyword(lower)) return DocType.INVOICE;

    return null;
  }

  /**
   * Step 3: LLM classification for ambiguous PDFs/images.
   * Sends first ~2000 chars of extracted text to Claude.
   */
  async classifyByLlm(content: string, filename: string, mimeType: string): Promise<DocType> {
    const preview = content.substring(0, 2000);

    const prompt = `You are classifying a freight/logistics document for automated processing.

File: ${filename}
MIME type: ${mimeType}

Content preview:
\`\`\`
${preview || '(no text content available)'}
\`\`\`

Classify this document into exactly one of these types:
- "tariff" — carrier pricing table (zones, weight bands, rates, surcharges)
- "invoice" — carrier invoice or credit note (invoice number, line items, total amount)
- "shipment_csv" — list of shipments/consignments (origins, destinations, weights, costs)
- "other" — cannot determine or does not fit above types

Respond with a single JSON object only, no explanation:
{"type": "tariff" | "invoice" | "shipment_csv" | "other"}`;

    const response = await this.anthropic.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 64,
      temperature: 0,
      messages: [{ role: 'user', content: prompt }],
    });

    const text = response.content.find((c) => c.type === 'text');
    if (!text || text.type !== 'text') return DocType.OTHER;

    try {
      // Strip markdown fences if present
      const raw = text.text.replace(/```json\n?|```/g, '').trim();
      const parsed = JSON.parse(raw) as { type: string };
      return this.toDocType(parsed.type);
    } catch {
      this.logger.warn({ event: 'llm_classification_parse_error', raw: text.text.substring(0, 200) });
      return DocType.OTHER;
    }
  }

  // ─── helpers ────────────────────────────────────────────────────────────────

  private getExtension(filename: string): string {
    const idx = filename.lastIndexOf('.');
    return idx >= 0 ? filename.substring(idx).toLowerCase() : '';
  }

  private hasInvoiceKeyword(lower: string): boolean {
    for (const kw of this.INVOICE_KEYWORDS) {
      if (lower.includes(kw)) return true;
    }
    // "rg" separated by non-alphanumeric chars (space, _, -, ., start/end of string)
    // Avoids false positives in words like "hergang", "programmiert"
    if (/(?:^|[^a-z0-9])rg(?:[^a-z0-9]|$)/.test(lower)) return true;
    return false;
  }

  private toDocType(value: string): DocType {
    switch (value) {
      case 'tariff':      return DocType.TARIFF;
      case 'invoice':     return DocType.INVOICE;
      case 'shipment_csv': return DocType.SHIPMENT_CSV;
      default:            return DocType.OTHER;
    }
  }

  /**
   * Map a user-provided sourceType to the closest DocType (user override, step 4).
   * invoice → invoice, rate_card → tariff, fleet_log → shipment_csv
   */
  sourceTypeToDocType(sourceType: string): string {
    switch (sourceType) {
      case 'invoice':   return DocType.INVOICE;
      case 'rate_card': return DocType.TARIFF;
      case 'fleet_log': return DocType.SHIPMENT_CSV;
      default:          return DocType.OTHER;
    }
  }

  isLlmAvailable(): boolean {
    return !!process.env.ANTHROPIC_API_KEY;
  }
}
