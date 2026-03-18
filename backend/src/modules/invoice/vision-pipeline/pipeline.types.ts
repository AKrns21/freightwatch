/**
 * Shared types for the 6-stage vision parsing pipeline (Issue #23)
 */

// ─── Stage 2: Page classification ───────────────────────────────────────────

export type PageType =
  | 'cover'             // Title / address page — skip after extracting header hints
  | 'line-item-table'   // Primary line-item grid
  | 'surcharge-appendix'// Diesel, toll, fee appendix rows
  | 'continuation';     // Continuation of the preceding table

export interface ClassifiedPage {
  page_number: number;  // 0-indexed
  page_type: PageType;
  /** Processed image (base64 PNG, grayscale+normalized from Stage 1) */
  image_base64: string;
  width: number;
  height: number;
}

// ─── Stage 3: Structured extraction ─────────────────────────────────────────

/** How a value was determined by the LLM */
export type FieldSource = 'direct_ocr' | 'llm_inferred' | 'missing';

/** A single extracted value with its source annotation */
export interface AnnotatedField<T = string | number | null> {
  value: T;
  src: FieldSource;
}

export interface ExtractedHeader {
  invoice_number:     AnnotatedField<string | null>;
  invoice_date:       AnnotatedField<string | null>; // YYYY-MM-DD
  carrier_name:       AnnotatedField<string | null>;
  customer_name:      AnnotatedField<string | null>;
  customer_number:    AnnotatedField<string | null>;
  total_net_amount:   AnnotatedField<number | null>;
  total_gross_amount: AnnotatedField<number | null>;
  currency:           AnnotatedField<string | null>;
}

export interface ExtractedLine {
  shipment_date:      AnnotatedField<string | null>;
  shipment_reference: AnnotatedField<string | null>;
  tour:               AnnotatedField<string | null>;
  origin_zip:         AnnotatedField<string | null>;
  origin_country:     AnnotatedField<string | null>;
  dest_zip:           AnnotatedField<string | null>;
  dest_country:       AnnotatedField<string | null>;
  weight_kg:          AnnotatedField<number | null>;
  unit_price:         AnnotatedField<number | null>;
  line_total:         AnnotatedField<number | null>;
  billing_type:       AnnotatedField<string | null>;
}

export interface PageExtractionResult {
  page_number: number;
  page_type: PageType;
  /** Populated for line-item-table pages (also cover if it has header info) */
  header?: ExtractedHeader;
  lines: ExtractedLine[];
  raw_issues: string[];
}

// ─── Stage 4: Cross-document validation ─────────────────────────────────────

export interface ValidationResult {
  valid: boolean;
  errors: string[];   // blockers
  warnings: string[]; // non-fatal
}

// ─── Stage 5: Confidence scoring ────────────────────────────────────────────

export interface ConfidenceScore {
  /** Overall document confidence (0.0 – 1.0) */
  overall: number;
  /** Fraction of fields tagged direct_ocr */
  direct_ocr_ratio: number;
  /** Fraction of required fields present */
  completeness_ratio: number;
  /** Per-field breakdown for the review UI */
  field_breakdown: Record<string, FieldSource>;
}

// ─── Stage 6: Review gate ────────────────────────────────────────────────────

export enum ReviewAction {
  AUTO_IMPORT      = 'auto_import',
  AUTO_IMPORT_FLAG = 'auto_import_flag',
  HOLD_FOR_REVIEW  = 'hold_for_review',
  REJECT           = 'reject',
}

// ─── Pipeline result ─────────────────────────────────────────────────────────

export interface PipelineResult {
  /** Flat header extracted from the document */
  header: ExtractedHeader;
  /** All line items merged from all non-cover pages */
  lines: ExtractedLine[];
  confidence: ConfidenceScore;
  validation: ValidationResult;
  review_action: ReviewAction;
  all_issues: string[];
}
