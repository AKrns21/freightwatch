/**
 * Frontend types for FreightWatch MVP
 * Field names are camelCase to match FastAPI's alias_generator=to_camel output.
 */

// Project types
export interface Project {
  id: string;
  tenantId: string;
  name: string;
  customerName: string | null;
  phase: string | null;
  status: string | null;
  consultantId?: string | null;
  metadata?: Record<string, unknown> | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface ProjectStats {
  projectId: string;
  uploadCount: number;
  shipmentCount: number;
  noteCount: number;
  reportCount: number;
}

// Upload types — matches UploadListItemResponse from backend
export interface Upload {
  id: string;
  tenantId: string;
  projectId: string | null;
  filename: string;
  fileHash: string;
  mimeType: string | null;
  docType: string | null;
  status: string | null;
  parseMethod: string | null;
  confidence: number | null;
  llmAnalysis?: Record<string, unknown> | null;
  parseErrors?: Record<string, unknown> | null;
  parsingIssues?: unknown[] | null;
  receivedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

// Response from POST /api/uploads
export interface UploadCreatedResponse {
  uploadId: string;
  status: string;
  filename: string;
  fileHash: string;
}

export interface UploadReviewData {
  upload: Upload;
  llmAnalysis: {
    fileType: string;
    confidence: number;
    description: string;
    structureAnalysis: unknown;
  };
  suggestedMappings: Array<{
    field: string;
    column: string;
    confidence: number;
    sampleValues: string[];
  }>;
  preview: Array<Record<string, unknown>>;
  qualityScore: number;
  parsingIssues?: ParsingIssue[];
}

export interface ParsingIssue {
  type: string;
  message: string;
  timestamp: string;
  carrierName?: string;
  placeholderCarrierId?: string;
  row?: number;
  rawData?: Record<string, unknown>;
  invoiceNumber?: string;
  lineNumber?: number;
  missingFields?: string[];
}

export interface ShipmentSummary {
  id: string;
  invoiceNumber: string | null;
  shipmentDate: string | null;
  referenceNumber: string | null;
  originZip: string | null;
  destZip: string | null;
  weightKg: number | null;
  currency: string | null;
  actualTotalAmount: number | null;
  completenessScore: number | null;
}

export interface CarrierOption {
  id: string;
  name: string;
  codeNorm: string;
}

// Report types
export interface Report {
  id: string;
  projectId: string;
  version: number;
  reportType: string;
  title: string | null;
  dataSnapshot: {
    version: number;
    generatedAt: string;
    project: {
      id: string;
      name: string;
      phase: string;
      status: string;
    };
    statistics: ProjectStatistics;
    dataCompleteness: number;
    topOverpays?: Array<{
      shipmentId: string;
      date: string;
      carrier: string;
      originZip: string;
      destZip: string;
      actualCost: number;
      expectedCost: number;
      delta: number;
      deltaPct: number;
    }>;
  };
  dataCompleteness: number | null;
  shipmentCount: number | null;
  dateRangeStart?: string | null;
  dateRangeEnd?: string | null;
  generatedBy: string | null;
  generatedAt: string | null;
  createdAt: string | null;
  notes?: string | null;
}

export interface ProjectStatistics {
  totalShipments: number;
  parsedShipments: number;
  benchmarkedShipments: number;
  completeShipments: number;
  partialShipments: number;
  missingShipments: number;
  dataCompletenessAvg: number;
  totalActualCost: number;
  totalExpectedCost: number;
  totalSavingsPotential: number;
  overpayRate: number;
  carriers: CarrierAggregation[];
}

export interface CarrierAggregation {
  carrierId: string;
  carrierName: string;
  shipmentCount: number;
  totalActualCost: number;
  totalExpectedCost: number;
  totalDelta: number;
  avgDeltaPct: number;
  overpayCount: number;
  underpayCount: number;
  marketCount: number;
  dataCompletenessAvg: number;
}
