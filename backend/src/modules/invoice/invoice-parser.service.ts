import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, IsNull } from 'typeorm';
import Anthropic from '@anthropic-ai/sdk';
import { InvoiceHeader } from './entities/invoice-header.entity';
import { InvoiceLine } from './entities/invoice-line.entity';
import { PdfVisionService, PdfExtractionResult } from './pdf-vision.service';
import { ParsingTemplate } from '@/modules/parsing/entities/parsing-template.entity';
import { LlmParserService } from '@/modules/parsing/services/llm-parser.service';

/** Expected JSON shape returned by Claude for a scanned invoice */
interface VisionInvoiceResponse {
  invoices: Array<{
    invoice_number: string;
    invoice_date: string; // ISO date string: YYYY-MM-DD
    carrier_name: string;
    customer_name?: string;
    customer_number?: string;
    total_net_amount?: number;
    total_gross_amount?: number;
    currency: string;
    lines: Array<{
      shipment_date?: string;
      shipment_reference?: string;
      tour?: string;
      origin_zip?: string;
      origin_country?: string;
      dest_zip?: string;
      dest_country?: string;
      weight_kg?: number;
      unit_price?: number;
      line_total?: number;
      billing_type?: string;
    }>;
  }>;
  confidence: number;
  issues: string[];
}

/**
 * Parsed invoice result
 */
export interface InvoiceParseResult {
  header: {
    invoice_number: string;
    invoice_date: Date;
    carrier_name: string;
    carrier_id?: string;
    customer_name?: string;
    customer_number?: string;
    total_amount?: number;
    currency: string;
    payment_terms?: string;
    due_date?: Date;
  };
  lines: {
    line_number?: number;
    shipment_date?: Date;
    shipment_reference?: string;
    origin_zip?: string;
    origin_country?: string;
    dest_zip?: string;
    dest_country?: string;
    weight_kg?: number;
    service_level?: string;
    base_amount?: number;
    diesel_amount?: number;
    toll_amount?: number;
    other_charges?: number;
    line_total?: number;
    currency: string;
  }[];
  parsing_method: 'template' | 'llm' | 'hybrid';
  confidence: number;
  issues: string[];
}

/**
 * InvoiceParserService - Parse carrier invoice PDFs
 *
 * Extracts header and line items from carrier invoices:
 * - Carrier-specific template parsers (DHL, FedEx, etc.)
 * - LLM fallback for unknown formats
 * - Validates structure and amounts
 * - Handles multi-page invoices
 *
 * Strategy:
 * 1. Detect carrier from filename/content
 * 2. Try carrier-specific template
 * 3. Fall back to LLM for unknown formats
 * 4. Validate totals and data consistency
 */
@Injectable()
export class InvoiceParserService {
  private readonly logger = new Logger(InvoiceParserService.name);

  private readonly anthropic: Anthropic;

  constructor(
    @InjectRepository(InvoiceHeader)
    private readonly headerRepo: Repository<InvoiceHeader>,
    @InjectRepository(InvoiceLine)
    private readonly lineRepo: Repository<InvoiceLine>,
    @InjectRepository(ParsingTemplate)
    private readonly templateRepo: Repository<ParsingTemplate>,
    private readonly llmParser: LlmParserService,
    private readonly pdfVision: PdfVisionService,
  ) {
    this.anthropic = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY || '',
    });
  }

  /**
   * Parse invoice PDF and extract header + lines
   */
  async parseInvoicePdf(
    fileBuffer: Buffer,
    context: {
      filename: string;
      carrier_id?: string;
      tenant_id: string;
      upload_id?: string;
      project_id?: string;
    }
  ): Promise<InvoiceParseResult> {
    this.logger.log({
      event: 'parse_invoice_pdf_start',
      filename: context.filename,
      carrier_id: context.carrier_id,
    });

    // Step 1: Extract PDF content – detect text vs. scanned image
    const pdfContent = await this.pdfVision.extractFromBuffer(fileBuffer);

    this.logger.log({
      event: 'pdf_extraction_complete',
      filename: context.filename,
      mode: pdfContent.mode,
      page_count: pdfContent.page_count,
    });

    // Step 2: Try template matching (text-mode only for now)
    if (pdfContent.mode === 'text') {
      const template = await this.findMatchingTemplate(
        context.filename,
        context.carrier_id,
        context.tenant_id,
      );

      if (template) {
        try {
          const result = await this.parseWithTemplate(pdfContent, template, context);

          this.logger.log({
            event: 'parse_invoice_template_success',
            filename: context.filename,
            template_id: template.id,
            line_count: result.lines.length,
          });

          return result;
        } catch (error) {
          this.logger.warn({
            event: 'parse_invoice_template_failed',
            filename: context.filename,
            template_id: template.id,
            error: (error as Error).message,
          });
          // Fall through to LLM
        }
      }
    }

    // Step 3: Vision path for scanned PDFs, LLM text path for unknown formats
    if (pdfContent.mode === 'vision') {
      const result = await this.parseWithVision(pdfContent, context);

      this.logger.log({
        event: 'parse_invoice_vision_success',
        filename: context.filename,
        line_count: result.lines.length,
        confidence: result.confidence,
      });

      return result;
    }

    // Step 4: Text-based LLM fallback
    if (!this.llmParser.isAvailable()) {
      throw new Error('No template match and LLM not available');
    }

    const result = await this.parseWithLlm(fileBuffer, context);

    this.logger.log({
      event: 'parse_invoice_llm_success',
      filename: context.filename,
      line_count: result.lines.length,
      confidence: result.confidence,
    });

    return result;
  }

  /**
   * Find matching template for invoice
   */
  private async findMatchingTemplate(
    filename: string,
    carrierId: string | undefined,
    tenantId: string
  ): Promise<ParsingTemplate | null> {
    const templates = await this.templateRepo.find({
      where: [
        {
          tenant_id: tenantId,
          template_category: 'invoice',
          deleted_at: IsNull(),
        },
        {
          tenant_id: IsNull(),
          template_category: 'invoice',
          deleted_at: IsNull(),
        },
      ],
      order: { usage_count: 'DESC' },
    });

    // Score templates
    let bestMatch: ParsingTemplate | null = null;
    let bestScore = 0;

    for (const template of templates) {
      let score = 0;

      // Carrier match (50%)
      if (carrierId && template.detection?.carrier_id === carrierId) {
        score += 0.5;
      }

      // Filename pattern match (30%)
      if (template.detection?.filename_pattern) {
        const regex = new RegExp(template.detection.filename_pattern, 'i');
        if (regex.test(filename)) {
          score += 0.3;
        }
      }

      // Tenant-specific bonus (10%)
      if (template.tenant_id === tenantId) {
        score += 0.1;
      }

      // Usage frequency bonus (10%)
      if (template.usage_count > 5) {
        score += 0.1;
      }

      if (score > bestScore) {
        bestScore = score;
        bestMatch = template;
      }
    }

    // Require at least 70% confidence
    return bestScore >= 0.7 ? bestMatch : null;
  }

  /**
   * Parse invoice using template (text-mode PDFs only)
   */
  private async parseWithTemplate(
    pdfContent: PdfExtractionResult,
    template: ParsingTemplate,
    _context: {
      filename: string;
      carrier_id?: string;
      tenant_id: string;
      upload_id?: string;
      project_id?: string;
    }
  ): Promise<InvoiceParseResult> {
    const pdfText = pdfContent.text ?? '';

    // Extract header
    const header = this.extractHeaderWithTemplate(pdfText, template.mappings?.header || {});

    // Extract lines
    const lines = this.extractLinesWithTemplate(pdfText, template.mappings?.lines || {});

    // Validate
    this.validateInvoiceData(header, lines);

    return {
      header,
      lines,
      parsing_method: 'template',
      confidence: 0.95,
      issues: [],
    };
  }

  /**
   * Parse invoice using LLM
   */
  private async parseWithLlm(
    fileBuffer: Buffer,
    _context: {
      filename: string;
      carrier_id?: string;
      tenant_id: string;
      upload_id?: string;
      project_id?: string;
    }
  ): Promise<InvoiceParseResult> {
    const llmResult = await this.llmParser.analyzeFile(fileBuffer, {
      filename: _context.filename,
      mime_type: 'application/pdf',
      content_preview: '',
    });

    // Extract invoice structure from LLM analysis
    const header = this.extractHeaderFromLlmResult(llmResult);
    const lines = this.extractLinesFromLlmResult(llmResult);

    this.validateInvoiceData(header, lines);

    return {
      header,
      lines,
      parsing_method: 'llm',
      confidence: llmResult.confidence,
      issues: llmResult.issues.map(
        (i: { message?: string; description?: string }) => i.message || i.description || String(i)
      ),
    };
  }

  /**
   * Parse scanned invoice pages using Claude Vision.
   *
   * All pages are sent in a single Claude message so the model has full
   * document context (header on first page, line items across remaining pages).
   * Claude returns a JSON object; we map it to InvoiceParseResult.
   *
   * One PDF can contain multiple stapled invoices (e.g. the MECU sample).
   * We flatten all invoices' lines into a single result – the caller is
   * responsible for splitting them if needed.
   */
  private async parseWithVision(
    pdfContent: PdfExtractionResult,
    context: {
      filename: string;
      carrier_id?: string;
      tenant_id: string;
      upload_id?: string;
      project_id?: string;
    }
  ): Promise<InvoiceParseResult> {
    const pages = pdfContent.pages ?? [];

    this.logger.log({
      event: 'parse_invoice_vision_start',
      filename: context.filename,
      page_count: pages.length,
    });

    // Build the content array: one image block per page, followed by the prompt
    const imageBlocks: Anthropic.ImageBlockParam[] = pages.map((page) => ({
      type: 'image',
      source: {
        type: 'base64',
        media_type: 'image/png',
        data: page.image_base64,
      },
    }));

    const promptBlock: Anthropic.TextBlockParam = {
      type: 'text',
      text: this.buildVisionPrompt(pages.length),
    };

    const response = await this.anthropic.messages.create({
      model: 'claude-sonnet-4-6',
      max_tokens: 16000,
      messages: [
        {
          role: 'user',
          content: [...imageBlocks, promptBlock],
        },
      ],
    });

    const raw = response.content
      .filter((b): b is Anthropic.TextBlock => b.type === 'text')
      .map((b) => b.text)
      .join('');

    this.logger.log({
      event: 'parse_invoice_vision_response',
      filename: context.filename,
      response_length: raw.length,
    });

    return this.parseVisionResponse(raw, context.filename);
  }

  /**
   * Prompt sent to Claude together with the page images.
   *
   * Design goals:
   * - One call for the entire document (header + all line items)
   * - Strict JSON output so we can parse without heuristics
   * - Handles PDFs with multiple stapled invoices (invoices[] array)
   * - Extracts PLZ from full address strings (e.g. "D-42551 Velbert" → "42551")
   */
  private buildVisionPrompt(pageCount: number): string {
    return `You are analyzing ${pageCount} page(s) of a scanned German freight carrier invoice (Frachtrechnung).

Extract all data and return ONLY a JSON object – no markdown, no explanation, no code fences.

The PDF may contain multiple separate invoices stapled together. Include each as a separate entry in the "invoices" array.

Required JSON structure:
{
  "invoices": [
    {
      "invoice_number": "string",
      "invoice_date": "YYYY-MM-DD",
      "carrier_name": "string",
      "customer_name": "string or null",
      "customer_number": "string or null",
      "total_net_amount": number or null,
      "total_gross_amount": number or null,
      "currency": "EUR",
      "lines": [
        {
          "shipment_date": "YYYY-MM-DD or null",
          "shipment_reference": "Auftragsnummer string or null",
          "tour": "Tour number string or null",
          "origin_zip": "5-digit PLZ extracted from Ladestelle address, e.g. 42551",
          "origin_country": "2-letter ISO code, default DE",
          "dest_zip": "5-digit PLZ extracted from Entladestelle address",
          "dest_country": "2-letter ISO code, default DE",
          "weight_kg": number or null,
          "unit_price": number or null,
          "line_total": number or null,
          "billing_type": "LA code, e.g. 200 or 201"
        }
      ]
    }
  ],
  "confidence": 0.0 to 1.0,
  "issues": ["list any data quality problems found"]
}

Rules:
- Dates: convert German format (dd.mm.yy or dd.mm.yyyy) to YYYY-MM-DD
- Numbers: remove thousand separators, use period as decimal separator
- PLZ extraction: from addresses like "D-42551 Velbert" extract "42551"
- If a field is not visible or illegible, use null
- Each line item corresponds to one shipment row (LA code + Menge + Preis + GesamtEUR row)
- Ignore cover sheets, booking stamps (GEBUCHT/BEZAHLT), and VAT summary rows`;
  }

  /**
   * Parse Claude's JSON response into InvoiceParseResult.
   * Takes the first invoice in the array as the primary result.
   * If multiple invoices are present, their lines are all included and
   * a warning is added to issues[].
   */
  private parseVisionResponse(raw: string, filename: string): InvoiceParseResult {
    let parsed: VisionInvoiceResponse;

    try {
      // Strip any accidental markdown fences
      const cleaned = raw.replace(/^```(?:json)?\n?/m, '').replace(/\n?```$/m, '').trim();
      parsed = JSON.parse(cleaned) as VisionInvoiceResponse;
    } catch {
      this.logger.error({
        event: 'parse_invoice_vision_json_error',
        filename,
        raw_preview: raw.slice(0, 200),
      });
      return {
        header: {
          invoice_number: 'PARSE_ERROR',
          invoice_date: new Date(),
          carrier_name: 'Unknown',
          currency: 'EUR',
        },
        lines: [],
        parsing_method: 'llm',
        confidence: 0,
        issues: ['Vision response could not be parsed as JSON'],
      };
    }

    const invoices = parsed.invoices ?? [];
    if (invoices.length === 0) {
      return {
        header: {
          invoice_number: 'NO_INVOICES_FOUND',
          invoice_date: new Date(),
          carrier_name: 'Unknown',
          currency: 'EUR',
        },
        lines: [],
        parsing_method: 'llm',
        confidence: parsed.confidence ?? 0,
        issues: ['No invoices found in vision response', ...(parsed.issues ?? [])],
      };
    }

    const primary = invoices[0];
    const issues = [...(parsed.issues ?? [])];

    if (invoices.length > 1) {
      issues.push(
        `PDF contains ${invoices.length} invoices; all lines included, headers from first invoice only`,
      );
    }

    // Merge lines from all invoices
    const allLines: InvoiceParseResult['lines'] = invoices.flatMap((inv) =>
      (inv.lines ?? []).map((line, idx) => ({
        line_number: idx + 1,
        shipment_date: line.shipment_date ? new Date(line.shipment_date) : undefined,
        shipment_reference: line.shipment_reference ?? undefined,
        origin_zip: line.origin_zip ?? undefined,
        origin_country: line.origin_country ?? 'DE',
        dest_zip: line.dest_zip ?? undefined,
        dest_country: line.dest_country ?? 'DE',
        weight_kg: line.weight_kg ?? undefined,
        base_amount: line.unit_price ?? undefined,
        line_total: line.line_total ?? undefined,
        currency: inv.currency ?? 'EUR',
      }))
    );

    return {
      header: {
        invoice_number: primary.invoice_number ?? 'UNKNOWN',
        invoice_date: primary.invoice_date ? new Date(primary.invoice_date) : new Date(),
        carrier_name: primary.carrier_name ?? 'Unknown',
        customer_name: primary.customer_name ?? undefined,
        customer_number: primary.customer_number ?? undefined,
        total_amount: primary.total_net_amount ?? undefined,
        currency: primary.currency ?? 'EUR',
      },
      lines: allLines,
      parsing_method: 'llm',
      confidence: parsed.confidence ?? 0.8,
      issues,
    };
  }

  /**
   * Extract header using template rules
   */
  private extractHeaderWithTemplate(
    _pdfText: string,
    _headerMappings: Record<string, unknown>
  ): InvoiceParseResult['header'] {
    // TODO: Implement template-based header extraction
    // Parse invoice number, date, amounts using regex patterns
    this.logger.warn('Template-based header extraction not yet implemented');

    return {
      invoice_number: 'UNKNOWN',
      invoice_date: new Date(),
      carrier_name: 'Unknown',
      currency: 'EUR',
    };
  }

  /**
   * Extract lines using template rules
   */
  private extractLinesWithTemplate(
    _pdfText: string,
    _lineMappings: Record<string, unknown>
  ): InvoiceParseResult['lines'] {
    // TODO: Implement template-based line extraction
    // Parse line items using table detection and regex
    this.logger.warn('Template-based line extraction not yet implemented');

    return [];
  }

  /**
   * Extract header from LLM result
   */
  private extractHeaderFromLlmResult(_llmResult: unknown): InvoiceParseResult['header'] {
    // TODO: Map LLM analysis to header structure
    return {
      invoice_number: 'UNKNOWN',
      invoice_date: new Date(),
      carrier_name: 'Unknown',
      currency: 'EUR',
    };
  }

  /**
   * Extract lines from LLM result
   */
  private extractLinesFromLlmResult(_llmResult: unknown): InvoiceParseResult['lines'] {
    // TODO: Map LLM analysis to line structure
    return [];
  }

  /**
   * Validate invoice data consistency
   */
  private validateInvoiceData(
    header: InvoiceParseResult['header'],
    lines: InvoiceParseResult['lines']
  ): void {
    if (!header.invoice_number) {
      throw new Error('Missing invoice number');
    }

    if (!header.invoice_date) {
      throw new Error('Missing invoice date');
    }

    if (lines.length === 0) {
      this.logger.warn('Invoice has no line items');
    }

    // Validate totals if available
    if (header.total_amount && lines.length > 0) {
      const calculatedTotal = lines.reduce((sum, line) => sum + (line.line_total || 0), 0);

      const diff = Math.abs(header.total_amount - calculatedTotal);
      const tolerance = 0.01; // €0.01 tolerance for rounding

      if (diff > tolerance) {
        this.logger.warn({
          event: 'invoice_total_mismatch',
          header_total: header.total_amount,
          calculated_total: calculatedTotal,
          difference: diff,
        });
      }
    }
  }

  /**
   * Import parsed invoice into database
   */
  async importInvoice(
    parseResult: InvoiceParseResult,
    tenantId: string,
    uploadId?: string,
    projectId?: string
  ): Promise<InvoiceHeader> {
    this.logger.log({
      event: 'import_invoice_start',
      invoice_number: parseResult.header.invoice_number,
      line_count: parseResult.lines.length,
    });

    // Create header
    const header = await this.headerRepo.save({
      tenant_id: tenantId,
      upload_id: uploadId,
      project_id: projectId,
      invoice_number: parseResult.header.invoice_number,
      invoice_date: parseResult.header.invoice_date,
      carrier_id: parseResult.header.carrier_id,
      carrier_name: parseResult.header.carrier_name,
      customer_name: parseResult.header.customer_name,
      customer_number: parseResult.header.customer_number,
      total_amount: parseResult.header.total_amount,
      currency: parseResult.header.currency,
      payment_terms: parseResult.header.payment_terms,
      due_date: parseResult.header.due_date,
      meta: {
        parsing_method: parseResult.parsing_method,
        confidence: parseResult.confidence,
        issues: parseResult.issues,
      },
    });

    // Create lines
    for (const lineData of parseResult.lines) {
      await this.lineRepo.save({
        tenant_id: tenantId,
        invoice_id: header.id,
        line_number: lineData.line_number,
        shipment_date: lineData.shipment_date,
        shipment_reference: lineData.shipment_reference,
        origin_zip: lineData.origin_zip,
        origin_country: lineData.origin_country || 'DE',
        dest_zip: lineData.dest_zip,
        dest_country: lineData.dest_country || 'DE',
        weight_kg: lineData.weight_kg,
        service_level: lineData.service_level,
        base_amount: lineData.base_amount,
        diesel_amount: lineData.diesel_amount,
        toll_amount: lineData.toll_amount,
        other_charges: lineData.other_charges,
        line_total: lineData.line_total,
        currency: lineData.currency,
        match_status: 'unmatched',
      });
    }

    this.logger.log({
      event: 'import_invoice_complete',
      invoice_id: header.id,
      invoice_number: header.invoice_number,
      line_count: parseResult.lines.length,
    });

    return header;
  }
}
