/**
 * LLM Parser Types
 *
 * Type definitions for LLM-based file analysis and parsing
 */

/**
 * File type detection result
 */
export type FileType =
  | 'shipment_list'
  | 'invoice'
  | 'tariff_table'
  | 'route_documentation'
  | 'unknown';

/**
 * Column mapping suggestion from LLM
 */
export interface ColumnMapping {
  column: string;              // Source column name or letter (e.g., "A", "Carrier Name")
  field: string;               // Target database field (e.g., "carrier_name", "origin_zip")
  confidence: number;          // 0.0 - 1.0
  pattern?: string;            // Transformation pattern if needed
  sample_values: string[];     // Sample values from the column
  data_type?: string;          // Detected data type (string, number, date)
}

/**
 * Data quality issue detected by LLM
 */
export interface DataQualityIssue {
  type: 'missing_data' | 'invalid_format' | 'inconsistent' | 'ambiguous';
  severity: 'low' | 'medium' | 'high' | 'critical';
  description: string;
  affected_rows?: number[];
  suggested_fix?: string;
}

/**
 * Tariff structure analysis (for tariff PDFs)
 */
export interface TariffStructure {
  carrier?: string;
  currency?: string;
  valid_from?: string;
  valid_until?: string;
  lane_type?: string;
  zones?: number[];
  weight_bands?: Array<{ min: number; max: number }>;
  has_diesel_surcharge?: boolean;
  has_toll?: boolean;
}

/**
 * Complete LLM analysis result
 */
export interface LlmParseResult {
  file_type: FileType;
  confidence: number;
  description: string;
  column_mappings: ColumnMapping[];
  tariff_structure?: TariffStructure;
  issues: DataQualityIssue[];
  suggested_actions: string[];
  needs_review: boolean;
  raw_analysis?: string;      // Raw LLM response for debugging
}

/**
 * LLM prompt context
 */
export interface LlmPromptContext {
  filename: string;
  mime_type: string;
  content_preview: string;     // First N rows/lines
  tenant_context?: {
    known_carriers?: string[];
    expected_fields?: string[];
    currency?: string;
    country?: string;
  };
}

/**
 * Analysis options
 */
export interface AnalysisOptions {
  max_tokens?: number;
  temperature?: number;
  include_samples?: boolean;
  sample_size?: number;
}
