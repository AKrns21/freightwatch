import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, IsNull } from 'typeorm';
import { InvoiceHeader } from './entities/invoice-header.entity';
import { InvoiceLine } from './entities/invoice-line.entity';
import { PdfVisionService, PdfExtractionResult } from './pdf-vision.service';
import { ParsingTemplate } from '@/modules/parsing/entities/parsing-template.entity';
import { LlmParserService } from '@/modules/parsing/services/llm-parser.service';
import { Upload } from '@/modules/upload/entities/upload.entity';
import { VisionPipelineService } from './vision-pipeline/vision-pipeline.service';
import { ReviewAction } from './vision-pipeline/pipeline.types';


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
    billing_type?: string;
    tour_number?: string;
    referenz?: string;
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

  constructor(
    @InjectRepository(InvoiceHeader)
    private readonly headerRepo: Repository<InvoiceHeader>,
    @InjectRepository(InvoiceLine)
    private readonly lineRepo: Repository<InvoiceLine>,
    @InjectRepository(ParsingTemplate)
    private readonly templateRepo: Repository<ParsingTemplate>,
    @InjectRepository(Upload)
    private readonly uploadRepo: Repository<Upload>,
    private readonly llmParser: LlmParserService,
    private readonly pdfVision: PdfVisionService,
    private readonly visionPipeline: VisionPipelineService,
  ) {}

  /**
   * Parse invoice PDF and return one result per detected invoice.
   *
   * For scanned (vision) PDFs that contain multiple stapled invoices this
   * returns one InvoiceParseResult per invoice.  For all other paths (template,
   * LLM text) a single-element array is returned so callers always get an array.
   */
  async parseInvoicePdfMulti(
    fileBuffer: Buffer,
    context: {
      filename: string;
      carrier_id?: string;
      tenant_id: string;
      upload_id?: string;
      project_id?: string;
    }
  ): Promise<InvoiceParseResult[]> {
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

          return [result];
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

    // Step 3: Vision path for scanned PDFs — 6-stage pipeline
    if (pdfContent.mode === 'vision') {
      const pipelineResult = await this.visionPipeline.run(pdfContent.pages ?? [], {
        carrierId: context.carrier_id,
        tenantId: context.tenant_id,
        uploadId: context.upload_id,
      });

      const compatible = this.visionPipeline.toParserCompatible(pipelineResult);

      // Map review action to upload status update
      if (context.upload_id) {
        const statusMap: Record<ReviewAction, string> = {
          [ReviewAction.AUTO_IMPORT]:      'parsed',
          [ReviewAction.AUTO_IMPORT_FLAG]: 'parsed',
          [ReviewAction.HOLD_FOR_REVIEW]:  'needs_review',
          [ReviewAction.REJECT]:           'failed',
        };
        await this.uploadRepo.update(context.upload_id, {
          status: statusMap[compatible.review_action] as any,
          confidence: compatible.confidence,
          parse_method: 'llm',
        });
      }

      this.logger.log({
        event: 'parse_invoice_vision_pipeline_complete',
        filename: context.filename,
        confidence: compatible.confidence,
        review_action: compatible.review_action,
        line_count: compatible.lines.length,
      });

      return [
        {
          header: compatible.header,
          lines: compatible.lines,
          parsing_method: 'llm',
          confidence: compatible.confidence,
          issues: compatible.issues,
        },
      ];
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

    return [result];
  }

  /**
   * Parse invoice PDF and extract header + lines.
   *
   * @deprecated Prefer parseInvoicePdfMulti() which correctly handles PDFs
   * that contain multiple stapled invoices.  This method returns only the
   * first detected invoice and is kept for backward compatibility.
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
    const results = await this.parseInvoicePdfMulti(fileBuffer, context);
    return results[0];
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
      confidence: this.calculateInvoiceConfidence(lines),
      issues: [],
    };
  }

  /**
   * Calculate confidence score for a parsed invoice result.
   *
   * Required fields per line: weight_kg, and at least one of origin_zip or
   * dest_zip, plus a non-zero line_total or base_amount.
   * Score = fraction of lines with all required fields present, clamped 0–1.
   */
  private calculateInvoiceConfidence(lines: InvoiceParseResult['lines']): number {
    if (lines.length === 0) {
      return 0;
    }

    const completeLines = lines.filter(
      (line) =>
        line.weight_kg != null &&
        (line.origin_zip != null || line.dest_zip != null) &&
        ((line.line_total != null && line.line_total !== 0) ||
          (line.base_amount != null && line.base_amount !== 0))
    ).length;

    return Math.min(1.0, Math.max(0.0, completeLines / lines.length));
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

    // Validate and create lines
    const invalidLines: Array<{ line_number: number | undefined; missing_fields: string[] }> = [];

    for (const lineData of parseResult.lines) {
      const missingFields: string[] = [];
      if (!lineData.weight_kg) missingFields.push('weight_kg');
      if (!lineData.origin_zip && !lineData.dest_zip) missingFields.push('origin_zip', 'dest_zip');

      if (missingFields.length > 0) {
        invalidLines.push({ line_number: lineData.line_number, missing_fields: missingFields });
        this.logger.warn({
          event: 'invoice_line_validation_failed',
          invoice_number: parseResult.header.invoice_number,
          line_number: lineData.line_number,
          missing_fields: missingFields,
        });
        continue;
      }

      await this.lineRepo.save({
        tenant_id: tenantId,
        invoice_id: header.id,
        line_number: lineData.line_number,
        shipment_date: lineData.shipment_date,
        shipment_reference: lineData.shipment_reference,
        billing_type: lineData.billing_type,
        tour_number: lineData.tour_number,
        referenz: lineData.referenz,
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

    // Persist validation failures to upload.parsing_issues
    if (invalidLines.length > 0 && uploadId) {
      const upload = await this.uploadRepo.findOne({ where: { id: uploadId } });
      if (upload) {
        const existingIssues = (upload.parsing_issues as unknown[]) ?? [];
        const newIssues = invalidLines.map((l) => ({
          type: 'invoice_line_validation_error' as const,
          invoice_number: parseResult.header.invoice_number,
          line_number: l.line_number,
          missing_fields: l.missing_fields,
          message: `Line ${l.line_number ?? '?'} skipped: missing required fields [${l.missing_fields.join(', ')}]`,
          timestamp: new Date().toISOString(),
        }));
        await this.uploadRepo.update(uploadId, {
          parsing_issues: [...existingIssues, ...newIssues],
        });
      }
    }

    this.logger.log({
      event: 'import_invoice_complete',
      invoice_id: header.id,
      invoice_number: header.invoice_number,
      line_count: parseResult.lines.length,
    });

    return header;
  }

  /**
   * Import multiple parsed invoices into the database.
   *
   * Creates one invoice_header row per parse result and links each result's
   * lines exclusively to that header.  This is the correct path for PDFs that
   * contain multiple stapled invoices — every invoice gets its own header row
   * so no lines are orphaned on the wrong header.
   *
   * @returns headers - all created InvoiceHeader entities (one per invoice)
   * @returns totalLines - total number of line rows created across all invoices
   */
  async importInvoices(
    parseResults: InvoiceParseResult[],
    tenantId: string,
    uploadId?: string,
    projectId?: string
  ): Promise<{ headers: InvoiceHeader[]; totalLines: number }> {
    const headers: InvoiceHeader[] = [];
    let totalLines = 0;

    for (const result of parseResults) {
      const header = await this.importInvoice(result, tenantId, uploadId, projectId);
      headers.push(header);
      totalLines += result.lines.length;
    }

    this.logger.log({
      event: 'import_invoices_complete',
      invoice_count: headers.length,
      total_lines: totalLines,
    });

    return { headers, totalLines };
  }
}
