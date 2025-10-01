/**
 * Frontend types for FreightWatch MVP v3
 * These types match the backend DTOs and entities
 */

// Project types
export interface Project {
  id: string;
  tenant_id: string;
  name: string;
  customer_name: string;
  phase: 'quick_check' | 'deep_dive' | 'final';
  status: 'draft' | 'in_progress' | 'review' | 'completed';
  metadata?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface ProjectStats {
  project_id: string;
  name: string;
  upload_count: number;
  shipment_count: number;
  avg_completeness: number;
  note_count: number;
  report_count: number;
  phase: string;
  status: string;
  created_at: string;
}

// Upload types
export interface Upload {
  id: string;
  tenant_id: string;
  project_id: string;
  filename: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  file_hash: string;
  storage_path: string;
  status: 'pending' | 'parsing' | 'parsed' | 'failed' | 'needs_review';
  parsing_strategy?: 'template' | 'llm' | 'manual';
  llm_analysis?: Record<string, any>;
  parse_error?: string;
  uploaded_at: string;
}

export interface UploadReviewData {
  upload: Upload;
  llm_analysis: {
    file_type: string;
    confidence: number;
    description: string;
    structure_analysis: any;
  };
  suggested_mappings: Array<{
    field: string;
    column: string;
    confidence: number;
    sample_values: string[];
  }>;
  preview: Array<Record<string, any>>;
  quality_score: number;
}

// Report types
export interface Report {
  id: string;
  project_id: string;
  version: number;
  report_type: string;
  title: string;
  data_snapshot: {
    version: number;
    generated_at: string;
    project: {
      id: string;
      name: string;
      phase: string;
      status: string;
    };
    statistics: ProjectStatistics;
    data_completeness: number;
    top_overpays?: Array<{
      shipment_id: string;
      date: string;
      carrier: string;
      origin_zip: string;
      dest_zip: string;
      actual_cost: number;
      expected_cost: number;
      delta: number;
      delta_pct: number;
    }>;
  };
  data_completeness: number;
  shipment_count: number;
  date_range_start?: string;
  date_range_end?: string;
  generated_by: string;
  generated_at: string;
  created_at: string;
  notes?: string;
}

export interface ProjectStatistics {
  total_shipments: number;
  parsed_shipments: number;
  benchmarked_shipments: number;
  complete_shipments: number;
  partial_shipments: number;
  missing_shipments: number;
  data_completeness_avg: number;
  total_actual_cost: number;
  total_expected_cost: number;
  total_savings_potential: number;
  overpay_rate: number;
  carriers: CarrierAggregation[];
}

export interface CarrierAggregation {
  carrier_id: string;
  carrier_name: string;
  shipment_count: number;
  total_actual_cost: number;
  total_expected_cost: number;
  total_delta: number;
  avg_delta_pct: number;
  overpay_count: number;
  underpay_count: number;
  market_count: number;
  data_completeness_avg: number;
}

// API Response wrapper
export interface ApiResponse<T> {
  success: boolean;
  data: T;
  meta?: {
    timestamp: string;
    tenant_id: string;
  };
}

export interface ApiError {
  success: false;
  error: {
    code: string;
    message: string;
    details?: any;
  };
}
