import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { TariffTable } from './entities/tariff-table.entity';
import { ParsingTemplate } from '../parsing/entities/parsing-template.entity';
import { LlmParserService } from '../parsing/services/llm-parser.service';

/**
 * Parsed tariff entry from PDF
 */
export interface TariffEntry {
  zone: number;
  weight_min: number;
  weight_max: number;
  base_amount: number;
  currency: string;
  service_level?: string;
}

/**
 * Tariff parsing result
 */
export interface TariffParseResult {
  carrier_id: string;
  carrier_name: string;
  lane_type: string;
  valid_from: Date;
  valid_until?: Date;
  entries: TariffEntry[];
  parsing_method: 'template' | 'llm' | 'hybrid';
  confidence: number;
  issues: string[];
}

/**
 * TariffPdfParserService - Parse carrier tariff PDFs
 *
 * Extracts pricing tables from carrier tariff documents:
 * - Template-based parsing for known formats (DHL, FedEx, etc.)
 * - LLM fallback for unknown formats
 * - Validates extracted data structure
 * - Supports multi-page tariff tables
 *
 * Strategy:
 * 1. Try template matching first (fast, deterministic)
 * 2. Fall back to LLM for unknown formats
 * 3. Validate and normalize extracted data
 */
@Injectable()
export class TariffPdfParserService {
  private readonly logger = new Logger(TariffPdfParserService.name);

  constructor(
    @InjectRepository(TariffTable)
    private readonly tariffRepo: Repository<TariffTable>,
    @InjectRepository(ParsingTemplate)
    private readonly templateRepo: Repository<ParsingTemplate>,
    private readonly llmParser: LlmParserService,
  ) {}

  /**
   * Parse tariff PDF and extract pricing table
   */
  async parseTariffPdf(
    fileBuffer: Buffer,
    context: {
      filename: string;
      carrier_id?: string;
      tenant_id: string;
    },
  ): Promise<TariffParseResult> {
    this.logger.log({
      event: 'parse_tariff_pdf_start',
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
          event: 'parse_tariff_template_success',
          filename: context.filename,
          template_id: template.id,
          entry_count: result.entries.length,
        });

        return result;
      } catch (error) {
        this.logger.warn({
          event: 'parse_tariff_template_failed',
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
      event: 'parse_tariff_llm_success',
      filename: context.filename,
      entry_count: result.entries.length,
      confidence: result.confidence,
    });

    return result;
  }

  /**
   * Find matching template for tariff PDF
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
          template_category: 'tariff',
          deleted_at: null,
        },
        {
          tenant_id: null,
          template_category: 'tariff',
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

      // Carrier match (40%)
      if (
        carrierId &&
        template.detection?.carrier_id === carrierId
      ) {
        score += 0.4;
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

      // Tenant-specific bonus (20%)
      if (template.tenant_id === tenantId) {
        score += 0.2;
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
   * Parse tariff using template
   */
  private async parseWithTemplate(
    fileBuffer: Buffer,
    template: ParsingTemplate,
    context: any,
  ): Promise<TariffParseResult> {
    // Extract text from PDF
    const pdfText = await this.extractTextFromPdf(fileBuffer);

    // Apply template extraction rules
    const entries = this.extractEntriesWithTemplate(
      pdfText,
      template.mappings,
    );

    // Validate entries
    this.validateEntries(entries);

    // Extract metadata
    const metadata = this.extractMetadata(pdfText, template);

    return {
      carrier_id: context.carrier_id || metadata.carrier_id,
      carrier_name: metadata.carrier_name,
      lane_type: metadata.lane_type || 'domestic_de',
      valid_from: metadata.valid_from || new Date(),
      valid_until: metadata.valid_until,
      entries,
      parsing_method: 'template',
      confidence: 0.95,
      issues: [],
    };
  }

  /**
   * Parse tariff using LLM
   */
  private async parseWithLlm(
    fileBuffer: Buffer,
    context: any,
  ): Promise<TariffParseResult> {
    const llmResult = await this.llmParser.analyzeFile(fileBuffer, {
      filename: context.filename,
      mime_type: 'application/pdf',
      content_preview: '',
      analysis_type: 'tariff_extraction',
    } as any);

    // Extract tariff structure from LLM analysis
    if (!llmResult.tariff_structure) {
      throw new Error('LLM did not extract tariff structure');
    }

    const entries = this.convertLlmStructureToEntries(
      llmResult.tariff_structure,
    );

    this.validateEntries(entries);

    const structure: any = llmResult.tariff_structure;
    return {
      carrier_id: context.carrier_id || 'unknown',
      carrier_name: structure.carrier_name || structure.carrier || 'Unknown',
      lane_type: structure.lane_type || 'domestic_de',
      valid_from: typeof structure.valid_from === 'string' ? new Date(structure.valid_from) : (structure.valid_from || new Date()),
      valid_until: structure.valid_until ? (typeof structure.valid_until === 'string' ? new Date(structure.valid_until) : structure.valid_until) : undefined,
      entries,
      parsing_method: 'llm',
      confidence: llmResult.confidence,
      issues: llmResult.issues.map((i: any) => i.message || i.description || String(i)),
    };
  }

  /**
   * Extract text from PDF buffer
   */
  private async extractTextFromPdf(buffer: Buffer): Promise<string> {
    // TODO: Implement PDF text extraction
    // Options:
    // - pdf-parse library
    // - pdfjs-dist
    // - External service (e.g., AWS Textract)

    this.logger.warn('PDF text extraction not yet implemented');
    return buffer.toString('utf-8');
  }

  /**
   * Extract tariff entries using template rules
   */
  private extractEntriesWithTemplate(
    pdfText: string,
    mappings: Record<string, any>,
  ): TariffEntry[] {
    const entries: TariffEntry[] = [];

    // TODO: Implement template-based extraction
    // Parse table structure based on template rules
    // Extract zone, weight ranges, prices

    this.logger.warn(
      'Template-based tariff extraction not yet implemented',
    );

    return entries;
  }

  /**
   * Convert LLM tariff structure to entries
   */
  private convertLlmStructureToEntries(
    structure: any,
  ): TariffEntry[] {
    const entries: TariffEntry[] = [];

    if (!structure.zones || !Array.isArray(structure.zones)) {
      return entries;
    }

    for (const zone of structure.zones) {
      if (!zone.weight_bands || !Array.isArray(zone.weight_bands)) {
        continue;
      }

      for (const band of zone.weight_bands) {
        entries.push({
          zone: zone.zone_number,
          weight_min: band.weight_min || 0,
          weight_max: band.weight_max || 999999,
          base_amount: band.price,
          currency: structure.currency || 'EUR',
          service_level: zone.service_level,
        });
      }
    }

    return entries;
  }

  /**
   * Extract metadata from PDF text
   */
  private extractMetadata(
    pdfText: string,
    template: ParsingTemplate,
  ): {
    carrier_id?: string;
    carrier_name?: string;
    lane_type?: string;
    valid_from?: Date;
    valid_until?: Date;
  } {
    // TODO: Implement metadata extraction
    // - Parse carrier name
    // - Parse validity dates (Gültig ab, Valid from)
    // - Detect lane type (domestic, international)

    return {};
  }

  /**
   * Validate extracted entries
   */
  private validateEntries(entries: TariffEntry[]): void {
    if (entries.length === 0) {
      throw new Error('No tariff entries extracted');
    }

    for (const entry of entries) {
      if (entry.zone < 0) {
        throw new Error(`Invalid zone: ${entry.zone}`);
      }

      if (entry.weight_min < 0 || entry.weight_max < entry.weight_min) {
        throw new Error(
          `Invalid weight range: ${entry.weight_min}-${entry.weight_max}`,
        );
      }

      if (entry.base_amount <= 0) {
        throw new Error(`Invalid price: ${entry.base_amount}`);
      }
    }
  }

  /**
   * Import parsed tariff into database
   */
  async importTariff(
    parseResult: TariffParseResult,
    tenantId: string,
  ): Promise<{ imported: number; skipped: number }> {
    this.logger.log({
      event: 'import_tariff_start',
      carrier_id: parseResult.carrier_id,
      entry_count: parseResult.entries.length,
    });

    let imported = 0;
    let skipped = 0;

    for (const entry of parseResult.entries) {
      // Check if entry already exists
      const existing = await this.tariffRepo.findOne({
        where: {
          tenant_id: tenantId,
          carrier_id: parseResult.carrier_id,
          lane_type: parseResult.lane_type,
          weight_min: entry.weight_min,
          weight_max: entry.weight_max,
          valid_from: parseResult.valid_from,
        } as any,
      });

      if (existing) {
        skipped++;
        continue;
      }

      // Create new tariff entry
      await this.tariffRepo.save({
        tenant_id: tenantId,
        carrier_id: parseResult.carrier_id,
        lane_type: parseResult.lane_type,
        zone: entry.zone,
        weight_min: entry.weight_min,
        weight_max: entry.weight_max,
        base_amount: entry.base_amount,
        currency: entry.currency,
        service_level: entry.service_level,
        valid_from: parseResult.valid_from,
        valid_until: parseResult.valid_until,
      });

      imported++;
    }

    this.logger.log({
      event: 'import_tariff_complete',
      carrier_id: parseResult.carrier_id,
      imported,
      skipped,
    });

    return { imported, skipped };
  }
}
