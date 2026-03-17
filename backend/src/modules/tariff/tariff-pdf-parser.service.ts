import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, IsNull } from 'typeorm';
import { TariffTable } from './entities/tariff-table.entity';
import { ParsingTemplate } from '@/modules/parsing/entities/parsing-template.entity';
import { LlmParserService } from '@/modules/parsing/services/llm-parser.service';

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
    private readonly llmParser: LlmParserService
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
    }
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
      context.tenant_id
    );

    if (template) {
      try {
        const result = await this.parseWithTemplate(fileBuffer, template, context);

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
    tenantId: string
  ): Promise<ParsingTemplate | null> {
    const templates = await this.templateRepo.find({
      where: [
        {
          tenant_id: tenantId,
          template_category: 'tariff',
          deleted_at: IsNull(),
        },
        {
          tenant_id: IsNull(),
          template_category: 'tariff',
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

      // Carrier match (40%)
      if (carrierId && template.detection?.carrier_id === carrierId) {
        score += 0.4;
      }

      // Filename pattern match (30%)
      if (template.detection?.filename_pattern) {
        const regex = new RegExp(template.detection.filename_pattern, 'i');
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
    context: any
  ): Promise<TariffParseResult> {
    // Extract text from PDF
    const pdfText = await this.extractTextFromPdf(fileBuffer);

    // Apply template extraction rules
    const entries = this.extractEntriesWithTemplate(pdfText, template.mappings);

    // Validate entries
    this.validateEntries(entries);

    // Extract metadata
    const metadata = this.extractMetadata(pdfText, template);

    return {
      carrier_id: context.carrier_id || metadata.carrier_id,
      carrier_name: metadata.carrier_name || 'Unknown',
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
  private async parseWithLlm(fileBuffer: Buffer, context: any): Promise<TariffParseResult> {
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

    const entries = this.convertLlmStructureToEntries(llmResult.tariff_structure);

    this.validateEntries(entries);

    const structure: any = llmResult.tariff_structure;
    return {
      carrier_id: context.carrier_id || 'unknown',
      carrier_name: structure.carrier_name || structure.carrier || 'Unknown',
      lane_type: structure.lane_type || 'domestic_de',
      valid_from:
        typeof structure.valid_from === 'string'
          ? new Date(structure.valid_from)
          : structure.valid_from || new Date(),
      valid_until: structure.valid_until
        ? typeof structure.valid_until === 'string'
          ? new Date(structure.valid_until)
          : structure.valid_until
        : undefined,
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
   * Extract tariff entries using template rules.
   *
   * Supports two extraction strategies selected by mappings.strategy:
   *
   * 1. "pre_parsed" (default when mappings.tariff_structure present)
   *    The template stores the full structured JSON produced by a prior LLM
   *    extraction in mappings.tariff_structure.  The structure follows the
   *    canonical sample-data format:
   *      { zones: [{zone_number, ...}], matrix: [{weight_from, weight_to, prices: {zone_N: price}}] }
   *    This is the common case for known carrier formats that were parsed once
   *    by the LLM and then promoted to a template.
   *
   * 2. "text_grid" (mappings.strategy === "text_grid")
   *    Parses the raw PDF text using configurable regex patterns.
   *    Required mappings fields:
   *      zone_count         - number of zone columns (e.g. 6)
   *      zone_header_regex  - regex that matches the header row; named
   *                           capture group "z1" through "zN" for zone labels
   *                           (default: matches "Zone 1  Zone 2 ..." style rows)
   *      weight_row_regex   - regex with named captures "wfrom", "wto", and
   *                           "p1"..."pN" for each zone price
   *                           (default: matches "bis NNN kg  P1  P2 ..." rows)
   *      currency           - ISO currency code (default: "EUR")
   *      service_level      - optional service level tag for all entries
   *
   * If neither strategy applies (mappings empty / unknown), returns [] so the
   * existing LLM fallback path triggers normally.
   */
  private extractEntriesWithTemplate(
    pdfText: string,
    mappings: Record<string, any>
  ): TariffEntry[] {
    if (!mappings || typeof mappings !== 'object') {
      this.logger.warn('Template mappings missing or invalid — falling through to LLM');
      return [];
    }

    // Strategy 1: pre-parsed JSON structure embedded in template mappings
    if (mappings.tariff_structure) {
      return this.extractFromPreParsedStructure(mappings.tariff_structure, mappings);
    }

    // Strategy 2: parse raw PDF text using regex patterns
    if (mappings.strategy === 'text_grid') {
      return this.extractFromTextGrid(pdfText, mappings);
    }

    this.logger.warn(
      'Template mappings have no recognized strategy (need tariff_structure or strategy="text_grid") — falling through to LLM'
    );
    return [];
  }

  /**
   * Extract entries from a pre-parsed tariff structure stored in the template.
   *
   * Expected structure (matches the canonical LLM extraction output):
   * {
   *   zones: [{ zone_number: 1, ... }, ...],
   *   matrix: [
   *     { weight_from: 1, weight_to: 200, prices: { zone_1: 52, zone_2: 61.5, ... } },
   *     ...
   *   ]
   * }
   *
   * The prices object uses keys of the form "zone_N" where N is the zone_number.
   * Null prices (special zones with only partial weight coverage) are skipped.
   */
  private extractFromPreParsedStructure(
    structure: Record<string, any>,
    mappings: Record<string, any>
  ): TariffEntry[] {
    const entries: TariffEntry[] = [];

    if (!structure.matrix || !Array.isArray(structure.matrix)) {
      this.logger.warn('Pre-parsed tariff structure missing matrix array');
      return [];
    }

    const currency: string = mappings.currency || structure.currency || 'EUR';
    const serviceLevel: string | undefined = mappings.service_level || undefined;

    for (const band of structure.matrix) {
      const weightFrom = Number(band.weight_from);
      const weightTo = Number(band.weight_to);

      if (isNaN(weightFrom) || isNaN(weightTo)) {
        this.logger.warn({ event: 'tariff_band_invalid_weight', band });
        continue;
      }

      if (!band.prices || typeof band.prices !== 'object') {
        continue;
      }

      for (const [key, rawPrice] of Object.entries(band.prices)) {
        // Keys are "zone_1", "zone_2", ... — extract zone number
        const zoneMatch = key.match(/^zone_(\d+)$/);
        if (!zoneMatch) {
          continue;
        }

        const zoneNumber = parseInt(zoneMatch[1], 10);
        const price = Number(rawPrice);

        // Skip null/missing prices (some zones only cover certain weight bands)
        if (rawPrice === null || rawPrice === undefined || isNaN(price) || price <= 0) {
          continue;
        }

        entries.push({
          zone: zoneNumber,
          weight_min: weightFrom,
          weight_max: weightTo,
          base_amount: price,
          currency,
          service_level: serviceLevel,
        });
      }
    }

    this.logger.log({
      event: 'tariff_pre_parsed_extraction_complete',
      entry_count: entries.length,
    });

    return entries;
  }

  /**
   * Extract entries by scanning raw PDF text with configurable regex patterns.
   *
   * This handles PDFs where text extraction yields a readable tabular layout.
   * The default patterns match the common German carrier format:
   *
   *   Header row:   "Zone I  Zone II  Zone III  ..."
   *   Weight rows:  "bis 200 kg  52,00  61,50  62,20  ..."
   *
   * Custom patterns can be supplied via mappings.zone_header_regex and
   * mappings.weight_row_regex.  Patterns must use named capture groups.
   *
   * Default zone_header_regex capture groups: z1, z2, ..., zN
   * Default weight_row_regex capture groups:  wto (upper bound), p1, p2, ..., pN
   *
   * mappings fields:
   *   zone_count         {number}   Required. Number of zone columns.
   *   weight_row_regex   {string}   Optional. Override default weight-row pattern.
   *   zone_header_regex  {string}   Optional. Override default zone-header pattern.
   *   currency           {string}   Optional. Default "EUR".
   *   service_level      {string}   Optional.
   *   weight_from_explicit {boolean} When true, weight_row_regex must supply "wfrom"
   *                                  capture; otherwise previous row's wto+1 is used.
   */
  private extractFromTextGrid(pdfText: string, mappings: Record<string, any>): TariffEntry[] {
    const entries: TariffEntry[] = [];
    const zoneCount: number = Number(mappings.zone_count);

    if (!zoneCount || zoneCount < 1) {
      this.logger.warn('text_grid strategy requires mappings.zone_count');
      return [];
    }

    const currency: string = mappings.currency || 'EUR';
    const serviceLevel: string | undefined = mappings.service_level || undefined;
    const lines = pdfText.split(/\r?\n/);

    // Build default patterns if not overridden.
    // Zone numbers are discovered dynamically from the header row rather than
    // assumed to be 1..N, because some tariffs have non-contiguous zone numbers.
    const zoneHeaderRegex: RegExp = mappings.zone_header_regex
      ? new RegExp(mappings.zone_header_regex, 'i')
      : /Zone\s+(?:I{1,3}|IV|V?I{0,3}|\d+)/i;

    // Default weight row: "bis NNN kg  P1  P2  P3 ..."
    // Prices may use European decimal notation (comma as decimal separator).
    const weightRowRegex: RegExp = mappings.weight_row_regex
      ? new RegExp(mappings.weight_row_regex, 'i')
      : /bis\s+([\d.,]+)\s*kg((?:\s+[\d.,]+)+)/i;

    // Discover zone numbers from header line
    const zoneNumbers: number[] = [];
    for (const line of lines) {
      if (zoneHeaderRegex.test(line)) {
        const zoneMatches = [...line.matchAll(/Zone\s+(\w+)/gi)];
        for (const m of zoneMatches) {
          const romanOrNum = m[1].trim();
          zoneNumbers.push(this.parseZoneLabel(romanOrNum));
        }
        if (zoneNumbers.length >= zoneCount) {
          break;
        }
      }
    }

    // If we found no zone headers, fall back to sequential 1..N
    if (zoneNumbers.length === 0) {
      for (let i = 1; i <= zoneCount; i++) {
        zoneNumbers.push(i);
      }
    }

    let prevWeightTo = 0;

    for (const line of lines) {
      const match = line.match(weightRowRegex);
      if (!match) {
        continue;
      }

      // Standard pattern: match[1] = upper weight bound, match[2] = price columns
      const weightTo = this.parseEuropeanNumber(match[1]);
      if (isNaN(weightTo) || weightTo <= 0) {
        continue;
      }

      const weightFrom = prevWeightTo > 0 ? prevWeightTo + 1 : 1;
      prevWeightTo = weightTo;

      // Parse price columns from the remainder of the match
      const priceTokens = (match[2] || '')
        .trim()
        .split(/\s+/)
        .map((t) => this.parseEuropeanNumber(t))
        .filter((p) => !isNaN(p));

      for (let i = 0; i < Math.min(priceTokens.length, zoneNumbers.length); i++) {
        const price = priceTokens[i];
        if (price <= 0) {
          continue;
        }

        entries.push({
          zone: zoneNumbers[i],
          weight_min: weightFrom,
          weight_max: weightTo,
          base_amount: price,
          currency,
          service_level: serviceLevel,
        });
      }
    }

    this.logger.log({
      event: 'tariff_text_grid_extraction_complete',
      entry_count: entries.length,
    });

    return entries;
  }

  /**
   * Parse a number that may use European decimal notation (comma as decimal).
   * E.g. "62,20" → 62.20, "1.234,56" → 1234.56, "52" → 52.
   */
  private parseEuropeanNumber(value: string): number {
    if (!value) {
      return NaN;
    }
    const cleaned = value.trim();

    // Both dot and comma present — determine which is the decimal separator
    const dotPos = cleaned.lastIndexOf('.');
    const commaPos = cleaned.lastIndexOf(',');

    let normalized: string;
    if (dotPos > -1 && commaPos > -1) {
      // Whichever comes last is the decimal separator
      normalized =
        dotPos > commaPos
          ? cleaned.replace(/,/g, '') // US: 1,234.56
          : cleaned.replace(/\./g, '').replace(',', '.'); // EU: 1.234,56
    } else if (commaPos > -1) {
      normalized = cleaned.replace(',', '.');
    } else {
      normalized = cleaned;
    }

    return parseFloat(normalized);
  }

  /**
   * Convert a zone label (Roman numeral or plain integer) to a zone number.
   * E.g. "I" → 1, "IV" → 4, "VII" → 7, "3" → 3.
   */
  private parseZoneLabel(label: string): number {
    const trimmed = label.trim().toUpperCase();

    // Try plain integer first
    const asInt = parseInt(trimmed, 10);
    if (!isNaN(asInt)) {
      return asInt;
    }

    // Roman numeral mapping (covers the range used in German carrier tariffs)
    const romanMap: Record<string, number> = {
      I: 1,
      II: 2,
      III: 3,
      IV: 4,
      V: 5,
      VI: 6,
      VII: 7,
      VIII: 8,
      IX: 9,
      X: 10,
    };

    return romanMap[trimmed] ?? 1;
  }

  /**
   * Convert LLM tariff structure to entries
   */
  private convertLlmStructureToEntries(structure: any): TariffEntry[] {
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
   * Extract metadata from PDF text and template mappings.
   *
   * Resolution order for each field:
   *   1. mappings.metadata.<field>  — static value hardcoded in the template
   *      (e.g. {"carrier_id": "cosi", "lane_type": "domestic_de"})
   *   2. mappings.tariff_structure.meta — structured LLM output embedded in template
   *   3. Regex scan of pdfText — best-effort fallback for plain-text PDFs
   *
   * Date patterns recognised: "dd.mm.yyyy", "dd/mm/yyyy", "yyyy-mm-dd".
   * Lane type heuristics: presence of "Österreich"/"Austria" → "domestic_at",
   * "Schweiz"/"Switzerland" → "domestic_ch", else → "domestic_de".
   */
  private extractMetadata(
    pdfText: string,
    template: ParsingTemplate
  ): {
    carrier_id?: string;
    carrier_name?: string;
    lane_type?: string;
    valid_from?: Date;
    valid_until?: Date;
  } {
    const result: {
      carrier_id?: string;
      carrier_name?: string;
      lane_type?: string;
      valid_from?: Date;
      valid_until?: Date;
    } = {};

    const mappings = (template.mappings as Record<string, any>) || {};

    // --- 1. Static metadata from template mappings ---
    const staticMeta: Record<string, any> = mappings.metadata || {};

    if (staticMeta.carrier_id) {
      result.carrier_id = String(staticMeta.carrier_id);
    }
    if (staticMeta.carrier_name) {
      result.carrier_name = String(staticMeta.carrier_name);
    }
    if (staticMeta.lane_type) {
      result.lane_type = String(staticMeta.lane_type);
    }
    if (staticMeta.valid_from) {
      const d = this.parseMetadataDate(String(staticMeta.valid_from));
      if (d) {
        result.valid_from = d;
      }
    }
    if (staticMeta.valid_until) {
      const d = this.parseMetadataDate(String(staticMeta.valid_until));
      if (d) {
        result.valid_until = d;
      }
    }

    // --- 2. Structured LLM extraction embedded in template ---
    const embeddedMeta: Record<string, any> = mappings.tariff_structure?.meta || {};

    if (!result.carrier_name && embeddedMeta.carrier_name) {
      result.carrier_name = String(embeddedMeta.carrier_name);
    }
    if (!result.valid_from && embeddedMeta.valid_from) {
      const d = this.parseMetadataDate(String(embeddedMeta.valid_from));
      if (d) {
        result.valid_from = d;
      }
    }
    if (!result.valid_until && embeddedMeta.valid_until) {
      const d = this.parseMetadataDate(String(embeddedMeta.valid_until));
      if (d) {
        result.valid_until = d;
      }
    }

    // --- 3. Regex scan of PDF text for anything still missing ---
    if (!result.valid_from || !result.valid_until || !result.carrier_name || !result.lane_type) {
      const textMeta = this.scanPdfTextForMetadata(pdfText);

      if (!result.carrier_name && textMeta.carrier_name) {
        result.carrier_name = textMeta.carrier_name;
      }
      if (!result.valid_from && textMeta.valid_from) {
        result.valid_from = textMeta.valid_from;
      }
      if (!result.valid_until && textMeta.valid_until) {
        result.valid_until = textMeta.valid_until;
      }
      if (!result.lane_type && textMeta.lane_type) {
        result.lane_type = textMeta.lane_type;
      }
    }

    return result;
  }

  /**
   * Scan plain PDF text for carrier name, validity dates, and lane type.
   * All matches are best-effort; callers must treat results as hints only.
   */
  private scanPdfTextForMetadata(pdfText: string): {
    carrier_name?: string;
    lane_type?: string;
    valid_from?: Date;
    valid_until?: Date;
  } {
    const result: {
      carrier_name?: string;
      lane_type?: string;
      valid_from?: Date;
      valid_until?: Date;
    } = {};

    if (!pdfText) {
      return result;
    }

    const text = pdfText;

    // --- Validity dates ---
    // Patterns: "Gültig ab 01.01.2023", "Valid from 2023-01-01", "Stand: 01.01.2023"
    const validFromPatterns = [
      /(?:Gültig\s+ab|Valid\s+from|Stand)[:\s]+(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2})/i,
      /(?:ab\s+dem\s+)(\d{1,2}\.\d{1,2}\.\d{4})/i,
    ];
    for (const pattern of validFromPatterns) {
      const m = text.match(pattern);
      if (m) {
        const d = this.parseMetadataDate(m[1]);
        if (d) {
          result.valid_from = d;
          break;
        }
      }
    }

    // Patterns: "Gültig bis 31.12.2023", "Valid until ..."
    const validUntilPatterns = [
      /(?:Gültig\s+bis|Valid\s+until|bis\s+zum)[:\s]+(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2})/i,
      /(\d{1,2}\.\d{1,2}\.\d{4})\s*\(Gültig/i,
    ];
    for (const pattern of validUntilPatterns) {
      const m = text.match(pattern);
      if (m) {
        const d = this.parseMetadataDate(m[1]);
        if (d) {
          result.valid_until = d;
          break;
        }
      }
    }

    // --- Carrier name ---
    // Look for common German logistics company suffixes near the start of the document
    const carrierPattern =
      /([A-ZÄÖÜ][A-Za-zäöüÄÖÜß\s&.,-]{3,60}(?:GmbH|AG|KG|OHG|e\.K\.|GmbH\s*&\s*Co\.\s*KG)[^\n]{0,40})/;
    const carrierMatch = text.match(carrierPattern);
    if (carrierMatch) {
      result.carrier_name = carrierMatch[1].trim().replace(/\s+/g, ' ');
    }

    // --- Lane type heuristic ---
    const textLower = text.toLowerCase();
    if (textLower.includes('österreich') || textLower.includes('austria')) {
      result.lane_type = 'domestic_at';
    } else if (textLower.includes('schweiz') || textLower.includes('switzerland')) {
      result.lane_type = 'domestic_ch';
    } else if (
      textLower.includes('deutschland') ||
      textLower.includes('germany') ||
      textLower.includes('stückgutversand')
    ) {
      result.lane_type = 'domestic_de';
    }

    return result;
  }

  /**
   * Parse a date string in dd.mm.yyyy, dd/mm/yyyy, or yyyy-mm-dd format.
   * Returns null for any unrecognised or invalid input.
   */
  private parseMetadataDate(value: string): Date | null {
    if (!value) {
      return null;
    }
    const trimmed = value.trim();

    // yyyy-mm-dd
    const isoMatch = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (isoMatch) {
      const d = new Date(
        parseInt(isoMatch[1], 10),
        parseInt(isoMatch[2], 10) - 1,
        parseInt(isoMatch[3], 10)
      );
      return isNaN(d.getTime()) ? null : d;
    }

    // dd.mm.yyyy or dd/mm/yyyy
    const deMatch = trimmed.match(/^(\d{1,2})[./](\d{1,2})[./](\d{4})$/);
    if (deMatch) {
      const day = parseInt(deMatch[1], 10);
      const month = parseInt(deMatch[2], 10);
      const year = parseInt(deMatch[3], 10);
      if (month < 1 || month > 12 || day < 1 || day > 31 || year < 1900 || year > 2100) {
        return null;
      }
      const d = new Date(year, month - 1, day);
      // Verify the date is valid (rejects e.g. Feb 31)
      if (d.getFullYear() === year && d.getMonth() === month - 1 && d.getDate() === day) {
        return d;
      }
    }

    return null;
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
        throw new Error(`Invalid weight range: ${entry.weight_min}-${entry.weight_max}`);
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
    tenantId: string
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
