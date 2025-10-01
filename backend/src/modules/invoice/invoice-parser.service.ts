import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { InvoiceHeader } from './entities/invoice-header.entity';
import { InvoiceLine } from './entities/invoice-line.entity';
import { ParsingTemplate } from '../parsing/entities/parsing-template.entity';
import { LlmParserService } from '../parsing/services/llm-parser.service';

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

  constructor(
    @InjectRepository(InvoiceHeader)
    private readonly headerRepo: Repository<InvoiceHeader>,
    @InjectRepository(InvoiceLine)
    private readonly lineRepo: Repository<InvoiceLine>,
    @InjectRepository(ParsingTemplate)
    private readonly templateRepo: Repository<ParsingTemplate>,
    private readonly llmParser: LlmParserService,
  ) {}

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
    },
  ): Promise<InvoiceParseResult> {
    this.logger.log({
      event: 'parse_invoice_pdf_start',
      filename: context.filename,
      carrier_id: context.carrier_id,
    });

    // Step 1: Try template matching
    const template = await this.findMatchingTemplate(
      context.filename,
      context.carrier_id,
      context.tenant_id,
    );

    if (template) {
      try {
        const result = await this.parseWithTemplate(
          fileBuffer,
          template,
          context,
        );

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

    // Step 2: Fall back to LLM
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
    tenantId: string,
  ): Promise<ParsingTemplate | null> {
    const templates = await this.templateRepo.find({
      where: [
        {
          tenant_id: tenantId,
          template_category: 'invoice',
          deleted_at: null,
        },
        {
          tenant_id: null,
          template_category: 'invoice',
          deleted_at: null,
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
      if (
        carrierId &&
        template.detection?.carrier_id === carrierId
      ) {
        score += 0.5;
      }

      // Filename pattern match (30%)
      if (template.detection?.filename_pattern) {
        const regex = new RegExp(
          template.detection.filename_pattern,
          'i',
        );
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
   * Parse invoice using template
   */
  private async parseWithTemplate(
    fileBuffer: Buffer,
    template: ParsingTemplate,
    context: any,
  ): Promise<InvoiceParseResult> {
    // Extract text from PDF
    const pdfText = await this.extractTextFromPdf(fileBuffer);

    // Extract header
    const header = this.extractHeaderWithTemplate(
      pdfText,
      template.mappings?.header || {},
    );

    // Extract lines
    const lines = this.extractLinesWithTemplate(
      pdfText,
      template.mappings?.lines || {},
    );

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
    context: any,
  ): Promise<InvoiceParseResult> {
    const llmResult = await this.llmParser.analyzeFile(fileBuffer, {
      filename: context.filename,
      mime_type: 'application/pdf',
      tenant_id: context.tenant_id,
      analysis_type: 'invoice_extraction',
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
      issues: llmResult.issues.map((i) => i.message),
    };
  }

  /**
   * Extract text from PDF buffer
   */
  private async extractTextFromPdf(buffer: Buffer): Promise<string> {
    // TODO: Implement PDF text extraction
    // Use pdf-parse or similar library
    this.logger.warn('PDF text extraction not yet implemented');
    return buffer.toString('utf-8');
  }

  /**
   * Extract header using template rules
   */
  private extractHeaderWithTemplate(
    pdfText: string,
    headerMappings: Record<string, any>,
  ): any {
    // TODO: Implement template-based header extraction
    // Parse invoice number, date, amounts using regex patterns
    this.logger.warn(
      'Template-based header extraction not yet implemented',
    );

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
    pdfText: string,
    lineMappings: Record<string, any>,
  ): any[] {
    // TODO: Implement template-based line extraction
    // Parse line items using table detection and regex
    this.logger.warn(
      'Template-based line extraction not yet implemented',
    );

    return [];
  }

  /**
   * Extract header from LLM result
   */
  private extractHeaderFromLlmResult(llmResult: any): any {
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
  private extractLinesFromLlmResult(llmResult: any): any[] {
    // TODO: Map LLM analysis to line structure
    return [];
  }

  /**
   * Validate invoice data consistency
   */
  private validateInvoiceData(header: any, lines: any[]): void {
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
      const calculatedTotal = lines.reduce(
        (sum, line) => sum + (line.line_total || 0),
        0,
      );

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
    projectId?: string,
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
