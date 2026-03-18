import { Injectable, Logger } from '@nestjs/common';
import { PdfPageImage } from '../pdf-vision.service';
import { PreProcessorService } from './pre-processor.service';
import { PageClassifierService } from './page-classifier.service';
import { StructuredExtractorService } from './structured-extractor.service';
import { CrossDocumentValidatorService } from './cross-document-validator.service';
import { ConfidenceScorerService } from './confidence-scorer.service';
import { ReviewGateService } from './review-gate.service';
import {
  ExtractedHeader,
  ExtractedLine,
  PageExtractionResult,
  PipelineResult,
  ReviewAction,
} from './pipeline.types';

/** Null-safe annotated field value extractor */
function val<T>(field: { value: T; src: string } | undefined): T | null {
  return field?.value ?? null;
}

/**
 * VisionPipelineService — Orchestrator
 *
 * Coordinates the 6-stage vision parsing pipeline for scanned invoice PDFs:
 *
 *  Stage 1 — PreProcessorService:         grayscale + normalize + sharpen
 *  Stage 2 — PageClassifierService:       cover|line-item-table|surcharge-appendix|continuation
 *  Stage 3 — StructuredExtractorService:  per-page Claude Sonnet extraction with field-src annotations
 *  Stage 4 — CrossDocumentValidatorService: total reconciliation + required fields + date/weight sanity
 *  Stage 5 — ConfidenceScorerService:     direct_ocr_ratio + completeness_ratio → overall score
 *  Stage 6 — ReviewGateService:           threshold routing + GoBD raw_extraction audit trail
 *
 * Returns `PipelineResult` — callers convert this to `InvoiceParseResult[]` for DB import.
 */
@Injectable()
export class VisionPipelineService {
  private readonly logger = new Logger(VisionPipelineService.name);

  constructor(
    private readonly preProcessor: PreProcessorService,
    private readonly pageClassifier: PageClassifierService,
    private readonly structuredExtractor: StructuredExtractorService,
    private readonly validator: CrossDocumentValidatorService,
    private readonly confidenceScorer: ConfidenceScorerService,
    private readonly reviewGate: ReviewGateService,
  ) {}

  async run(
    rawPages: PdfPageImage[],
    opts: {
      carrierId?: string;
      tenantId?: string;
      uploadId?: string;
    } = {}
  ): Promise<PipelineResult> {
    const { carrierId, tenantId, uploadId } = opts;

    this.logger.log({
      event: 'vision_pipeline_start',
      page_count: rawPages.length,
      carrier_id: carrierId,
      upload_id: uploadId,
    });

    // ── Stage 1: Pre-processing ───────────────────────────────────────────
    const processedPages = await this.preProcessor.processPages(rawPages);

    // ── Stage 2: Page classification ─────────────────────────────────────
    const classifiedPages = await this.pageClassifier.classifyPages(processedPages);

    // ── Stage 3: Structured extraction (per page) ─────────────────────────
    const pageResults = await this.structuredExtractor.extractPages(
      classifiedPages,
      carrierId
    );

    // ── Merge: best header + all lines ────────────────────────────────────
    const { header, lines, issues } = this.mergePageResults(pageResults);

    // ── Stage 4: Cross-document validation ───────────────────────────────
    const validation = this.validator.validate(header, lines);

    // ── Stage 5: Confidence scoring ───────────────────────────────────────
    const confidence = this.confidenceScorer.score(header, lines);

    // ── Stage 6: Review gate ──────────────────────────────────────────────
    const reviewAction = this.reviewGate.decide(confidence, validation);

    // Persist raw extraction audit trail (non-blocking)
    if (tenantId && uploadId) {
      await this.reviewGate.persistRawExtraction({
        tenantId,
        uploadId,
        payload: { header, lines, page_results: pageResults },
        confidence: confidence.overall,
        issues: [...issues, ...validation.errors, ...validation.warnings],
      });
    }

    this.logger.log({
      event: 'vision_pipeline_complete',
      confidence: confidence.overall,
      direct_ocr_ratio: confidence.direct_ocr_ratio,
      line_count: lines.length,
      review_action: reviewAction,
      validation_errors: validation.errors.length,
      validation_warnings: validation.warnings.length,
    });

    return {
      header,
      lines,
      confidence,
      validation,
      review_action: reviewAction,
      all_issues: [
        ...issues,
        ...validation.errors.map((e) => `[ERROR] ${e}`),
        ...validation.warnings.map((w) => `[WARN] ${w}`),
      ],
    };
  }

  // ─── Merge helper ─────────────────────────────────────────────────────────

  /**
   * Merge page-level results into a single header + line list.
   *
   * Header strategy: use the first page that has a non-null invoice_number;
   * merge remaining header fields from other pages to fill nulls.
   *
   * Line strategy: collect all lines from non-cover pages in page order.
   */
  private mergePageResults(pages: PageExtractionResult[]): {
    header: ExtractedHeader;
    lines: ExtractedLine[];
    issues: string[];
  } {
    const allIssues: string[] = [];

    // Collect headers with invoice numbers first, then fall back to any header
    const headersWithNumber = pages
      .filter((p) => p.header?.invoice_number?.value != null)
      .map((p) => p.header!);

    const allHeaders = pages.filter((p) => p.header != null).map((p) => p.header!);

    // Start with the first header that has an invoice number, or the very first header
    const primaryHeader = headersWithNumber[0] ?? allHeaders[0];

    if (!primaryHeader) {
      // Nothing extracted at all — return empty annotated header
      allIssues.push('No header information found in any page');
      return {
        header: this.emptyHeader(),
        lines: [],
        issues: allIssues,
      };
    }

    // Merge: fill nulls in primary header from subsequent pages
    const mergedHeader: ExtractedHeader = { ...primaryHeader };
    const remainingHeaders = [...headersWithNumber, ...allHeaders].slice(1);

    for (const h of remainingHeaders) {
      for (const key of Object.keys(mergedHeader) as Array<keyof ExtractedHeader>) {
        if (mergedHeader[key].value == null && h[key].value != null) {
          (mergedHeader as unknown as Record<string, unknown>)[key] = h[key];
        }
      }
    }

    // Collect lines from all non-cover pages
    const lines: ExtractedLine[] = [];
    for (const page of pages) {
      if (page.page_type !== 'cover') {
        lines.push(...page.lines);
      }
      allIssues.push(...page.raw_issues);
    }

    return { header: mergedHeader, lines, issues: allIssues };
  }

  /** Returns a fully-annotated empty header (all fields missing) */
  private emptyHeader(): ExtractedHeader {
    const missing = <T>(): { value: T | null; src: 'missing' } => ({
      value: null,
      src: 'missing',
    });

    return {
      invoice_number:     missing(),
      invoice_date:       missing(),
      carrier_name:       missing(),
      customer_name:      missing(),
      customer_number:    missing(),
      total_net_amount:   missing(),
      total_gross_amount: missing(),
      currency:           { value: 'EUR', src: 'llm_inferred' },
    };
  }

  /**
   * Convert a `PipelineResult` back to the flat format used by InvoiceParserService.
   * This adapter is the only coupling point between the new pipeline and the
   * existing import/DB logic.
   */
  toParserCompatible(result: PipelineResult): {
    header: {
      invoice_number: string;
      invoice_date: Date;
      carrier_name: string;
      customer_name?: string;
      customer_number?: string;
      total_amount?: number;
      currency: string;
    };
    lines: Array<{
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
      base_amount?: number;
      line_total?: number;
      currency: string;
    }>;
    confidence: number;
    issues: string[];
    review_action: ReviewAction;
  } {
    const h = result.header;

    return {
      header: {
        invoice_number: val(h.invoice_number) ?? 'UNKNOWN',
        invoice_date: h.invoice_date.value ? new Date(h.invoice_date.value) : new Date(),
        carrier_name: val(h.carrier_name) ?? 'Unknown',
        customer_name: val(h.customer_name) ?? undefined,
        customer_number: val(h.customer_number) ?? undefined,
        total_amount: val(h.total_net_amount) ?? undefined,
        currency: val(h.currency) ?? 'EUR',
      },
      lines: result.lines.map((l, idx) => ({
        line_number: idx + 1,
        shipment_date: l.shipment_date.value ? new Date(l.shipment_date.value) : undefined,
        shipment_reference: val(l.shipment_reference) ?? undefined,
        billing_type: val(l.billing_type) ?? undefined,
        tour_number: val(l.tour) ?? undefined,
        referenz: val(l.shipment_reference) ?? undefined,
        origin_zip: val(l.origin_zip) ?? undefined,
        origin_country: val(l.origin_country) ?? 'DE',
        dest_zip: val(l.dest_zip) ?? undefined,
        dest_country: val(l.dest_country) ?? 'DE',
        weight_kg: val(l.weight_kg) ?? undefined,
        base_amount: val(l.unit_price) ?? undefined,
        line_total: val(l.line_total) ?? undefined,
        currency: val(h.currency) ?? 'EUR',
      })),
      confidence: result.confidence.overall,
      issues: result.all_issues,
      review_action: result.review_action,
    };
  }
}
