# Project Workflow Guide (Phase 4.1)

This document describes the project-centric workflow introduced in Phase 4.1, which shifts from upload-centric to project-based cost analysis.

## Overview

**Phase 4.1** introduces a project management layer that groups uploads, tracks workflow state, enables human review, and implements LLM-powered parsing.

### Key Changes

**Before Phase 4.1 (Upload-Centric):**
```
Upload → Parse → Calculate → Report
```
- Each upload was independent
- No workflow state tracking
- No human review process
- Hardcoded carrier mappings

**After Phase 4.1 (Project-Centric):**
```
Project → Uploads → Parse (LLM) → Review → Calculate → Report
```
- Multiple uploads grouped under projects
- Workflow state machine (draft → in_review → approved → finalized)
- Human review with consultant notes
- LLM-powered carrier detection
- Manual mapping with confidence scoring

## Core Concepts

### Project Entity

Projects are the top-level organizational unit for cost analysis.

```typescript
interface Project {
  id: string;
  tenant_id: string;

  // Identity
  name: string;              // e.g., "Q4 2023 Cost Analysis"
  description: string | null;

  // Workflow
  status: 'draft' | 'in_review' | 'approved' | 'finalized';

  // Timeline
  period_start: Date;        // Analysis period
  period_end: Date;

  // Metadata
  created_by: string;        // User UUID
  created_at: Date;
  updated_at: Date;
  deleted_at: Date | null;   // Soft delete
}
```

**Status Flow:**
1. **draft**: Initial creation, uploads being added
2. **in_review**: Ready for consultant review
3. **approved**: Consultant approved mappings/data
4. **finalized**: Report generated, project locked

### Upload Changes

Uploads are now linked to projects:

```typescript
interface Upload {
  id: string;
  tenant_id: string;
  project_id: string;        // NEW: Link to project

  // File metadata
  file_name: string;
  file_hash: string;
  file_size: number;
  mime_type: string;

  // Parsing metadata (NEW)
  parse_metadata: {
    detected_carrier?: string;
    confidence_score?: number;
    column_mapping?: Record<string, string>;
    sample_data?: any[];
  } | null;

  // Status
  status: 'pending' | 'parsing' | 'parsed' | 'error';
  error_message: string | null;

  // Timestamps
  created_at: Date;
  processed_at: Date | null;
}
```

## LLM-Powered Parsing Workflow

### 1. File Upload with Carrier Detection

```typescript
POST /api/projects/:project_id/uploads

1. User uploads file (CSV/Excel/PDF)
2. Calculate SHA256 hash
3. Check deduplication (file_hash + tenant_id)
4. Save file to storage
5. Create upload record with status='pending'
6. Enqueue parse job with LLM detection enabled
```

### 2. LLM Carrier Detection

```typescript
// In parse worker
const llmParser = new LLMParserService();

// Extract file structure
const structure = await extractStructure(file);
// { headers: ['Date', 'Carrier', 'Service', ...], sample_data: [...] }

// Send to LLM
const detection = await llmParser.detectCarrier({
  file_type: 'csv',
  headers: structure.headers,
  sample_data: structure.sample_data.slice(0, 10),
  tenant_id: tenantId
});

// Response:
{
  carrier: {
    name: "DHL Express",
    normalized_name: "DHL",
    confidence: 0.95,
    reasoning: "Header 'Carrier' contains 'DHL', service patterns match DHL Express"
  },
  service: {
    detected: "Premium",
    normalized: "PREMIUM",
    confidence: 0.87,
    reasoning: "Service column contains 'Premium' which maps to express delivery"
  },
  column_mapping: {
    date: "Shipment Date",
    origin_zip: "From PLZ",
    dest_zip: "To PLZ",
    weight_kg: "Weight (kg)"
  }
}
```

### 3. Confidence-Based Routing

```typescript
if (detection.carrier.confidence >= 0.80) {
  // High confidence: Auto-map
  const carrier = await findOrCreateCarrier({
    tenant_id: tenantId,
    name: detection.carrier.normalized_name
  });

  // Store auto-mapping
  await manualMappingRepo.save({
    tenant_id: tenantId,
    upload_id: uploadId,
    original_carrier_name: detection.carrier.name,
    carrier_id: carrier.id,
    confidence_score: detection.carrier.confidence,
    reviewed_by: null,  // Auto-mapped
    reviewed_at: new Date()
  });

  // Save template for future recognition
  await templateService.savePattern({
    tenant_id: tenantId,
    file_structure_hash: hashStructure(structure),
    carrier_id: carrier.id,
    column_mapping: detection.column_mapping
  });

  // Continue parsing
  await parseShipments(file, carrier, detection.column_mapping);

} else {
  // Low confidence: Flag for human review
  await upload.update({
    status: 'parsed',
    parse_metadata: {
      detected_carrier: detection.carrier.name,
      confidence_score: detection.carrier.confidence,
      requires_review: true
    }
  });

  // Notify consultant
  await notificationService.send({
    type: 'UPLOAD_NEEDS_REVIEW',
    project_id: projectId,
    upload_id: uploadId,
    message: `Low confidence carrier detection (${detection.carrier.confidence})`
  });
}
```

## Manual Mapping Table

Stores human-reviewed carrier and service mappings.

```sql
CREATE TABLE manual_mapping (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  upload_id UUID NOT NULL REFERENCES upload(id),
  project_id UUID REFERENCES project(id),

  -- Original parsed values
  original_carrier_name TEXT,
  original_service_name TEXT,

  -- Corrected mappings
  carrier_id UUID REFERENCES carrier(id),
  service_level VARCHAR(50),

  -- Review metadata
  reviewed_by UUID REFERENCES "user"(id),  -- NULL if auto-mapped by LLM
  reviewed_at TIMESTAMPTZ DEFAULT NOW(),
  confidence_score DECIMAL(3,2),
  review_notes TEXT,

  created_at TIMESTAMPTZ DEFAULT NOW(),

  CONSTRAINT fk_manual_mapping_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id)
);

CREATE INDEX idx_manual_mapping_tenant ON manual_mapping(tenant_id);
CREATE INDEX idx_manual_mapping_upload ON manual_mapping(upload_id);
CREATE INDEX idx_manual_mapping_project ON manual_mapping(project_id);
```

### Example Records

```sql
-- Auto-mapped by LLM (high confidence)
INSERT INTO manual_mapping (
  tenant_id, upload_id, project_id,
  original_carrier_name, carrier_id,
  reviewed_by, confidence_score
) VALUES (
  'tenant-uuid', 'upload-uuid', 'project-uuid',
  'DHL Express GmbH', 'dhl-carrier-uuid',
  NULL, 0.95
);

-- Manually corrected by consultant
INSERT INTO manual_mapping (
  tenant_id, upload_id, project_id,
  original_carrier_name, carrier_id,
  reviewed_by, confidence_score, review_notes
) VALUES (
  'tenant-uuid', 'upload-uuid', 'project-uuid',
  'GW Logistics', 'gebrueder-weiss-uuid',
  'consultant-user-uuid', 0.65,
  'LLM detected as "GW" which is Gebrüder Weiss based on context'
);
```

## Parsing Template Learning

Successful mappings are stored as templates to reduce future LLM calls.

```sql
CREATE TABLE parsing_template (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID REFERENCES carrier(id),

  -- File structure fingerprint
  file_structure_hash VARCHAR(64) NOT NULL,  -- SHA256 of headers + sample row structure

  -- Template data
  column_mapping JSONB NOT NULL,
  detection_metadata JSONB,

  -- Usage tracking
  match_count INTEGER DEFAULT 0,
  last_used_at TIMESTAMPTZ,

  created_at TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(tenant_id, file_structure_hash)
);

CREATE INDEX idx_template_tenant ON parsing_template(tenant_id);
CREATE INDEX idx_template_hash ON parsing_template(file_structure_hash);
```

### Template Matching Flow

```typescript
// Before invoking LLM, check for existing template
const structureHash = hashFileStructure(structure);

const template = await templateRepo.findOne({
  tenant_id: tenantId,
  file_structure_hash: structureHash
});

if (template) {
  // Template found! Use cached mapping
  logger.info({
    event: 'template_match',
    upload_id: uploadId,
    carrier_id: template.carrier_id,
    match_count: template.match_count
  });

  // Update usage
  await template.update({
    match_count: template.match_count + 1,
    last_used_at: new Date()
  });

  // Use template's column mapping
  return {
    carrier_id: template.carrier_id,
    column_mapping: template.column_mapping,
    source: 'template'
  };

} else {
  // No template: Invoke LLM
  const detection = await llmParser.detectCarrier(structure);

  // If successful, save as template
  if (detection.carrier.confidence >= 0.80) {
    await templateRepo.save({
      tenant_id: tenantId,
      carrier_id: detection.carrier.id,
      file_structure_hash: structureHash,
      column_mapping: detection.column_mapping,
      detection_metadata: detection
    });
  }

  return detection;
}
```

## Consultant Review Workflow

### Consultant Notes

Consultants can add notes to projects for quality issues, observations, and recommendations.

```sql
CREATE TABLE consultant_note (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  project_id UUID NOT NULL REFERENCES project(id),
  upload_id UUID REFERENCES upload(id),
  shipment_id UUID REFERENCES shipment(id),

  -- Note content
  category VARCHAR(50) NOT NULL,  -- 'data_quality', 'mapping_issue', 'recommendation', etc.
  severity VARCHAR(20) NOT NULL,  -- 'info', 'warning', 'critical'
  title VARCHAR(255) NOT NULL,
  description TEXT NOT NULL,

  -- Resolution
  status VARCHAR(20) DEFAULT 'open',  -- 'open', 'in_progress', 'resolved', 'wont_fix'
  resolved_at TIMESTAMPTZ,
  resolution_notes TEXT,

  -- Metadata
  created_by UUID NOT NULL REFERENCES "user"(id),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),

  CONSTRAINT fk_consultant_note_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id)
);
```

### Example Notes

```sql
-- Data quality issue
INSERT INTO consultant_note (
  tenant_id, project_id, upload_id,
  category, severity, title, description, created_by
) VALUES (
  'tenant-uuid', 'project-uuid', 'upload-uuid',
  'data_quality', 'warning',
  'Missing toll amounts',
  '45% of shipments have NULL toll_amount. Recommend contacting carrier for complete data.',
  'consultant-uuid'
);

-- Mapping correction
INSERT INTO consultant_note (
  tenant_id, project_id, shipment_id,
  category, severity, title, description, created_by
) VALUES (
  'tenant-uuid', 'project-uuid', 'shipment-uuid',
  'mapping_issue', 'critical',
  'Incorrect carrier mapping',
  'Shipment mapped to DHL but invoice clearly shows Gebrüder Weiss. Corrected in manual_mapping.',
  'consultant-uuid'
);
```

## API Endpoints

### Project Management

```typescript
// Create project
POST /api/projects
{
  name: "Q4 2023 Cost Analysis",
  description: "Quarterly review for MECU shipments",
  period_start: "2023-10-01",
  period_end: "2023-12-31"
}

// List projects
GET /api/projects
GET /api/projects?status=in_review

// Get project details
GET /api/projects/:id

// Update project status
PATCH /api/projects/:id/status
{
  status: "in_review"
}

// Add uploads to project
POST /api/projects/:id/uploads
(multipart file upload)

// List project uploads
GET /api/projects/:id/uploads

// Finalize project (locks data, generates report)
POST /api/projects/:id/finalize
```

### Manual Mapping Review

```typescript
// List uploads needing review
GET /api/projects/:id/uploads?requires_review=true

// Get upload parsing details
GET /api/uploads/:id/parse-details

// Approve/correct mapping
POST /api/uploads/:id/mapping
{
  carrier_id: "carrier-uuid",
  service_level: "PREMIUM",
  review_notes: "Confirmed DHL Premium based on invoice header"
}

// Bulk approve auto-mappings
POST /api/projects/:id/approve-auto-mappings
```

### Consultant Notes

```typescript
// Add note
POST /api/projects/:id/notes
{
  category: "data_quality",
  severity: "warning",
  title: "Incomplete toll data",
  description: "...",
  upload_id: "upload-uuid"  // Optional
}

// List notes
GET /api/projects/:id/notes
GET /api/projects/:id/notes?status=open&severity=critical

// Update note
PATCH /api/notes/:id
{
  status: "resolved",
  resolution_notes: "Customer provided updated file with toll data"
}
```

## Workflow State Machine

```typescript
enum ProjectStatus {
  DRAFT = 'draft',           // Initial state
  IN_REVIEW = 'in_review',   // Ready for consultant review
  APPROVED = 'approved',     // Consultant approved
  FINALIZED = 'finalized'    // Report generated, locked
}

const transitions = {
  draft: ['in_review'],
  in_review: ['draft', 'approved'],
  approved: ['in_review', 'finalized'],
  finalized: []  // Terminal state
};

async function transitionProject(
  projectId: string,
  newStatus: ProjectStatus
): Promise<void> {
  const project = await projectRepo.findOne({ id: projectId });

  if (!transitions[project.status].includes(newStatus)) {
    throw new BadRequestException(
      `Cannot transition from ${project.status} to ${newStatus}`
    );
  }

  // Validation
  if (newStatus === 'in_review') {
    // Must have at least one upload
    const uploadCount = await uploadRepo.count({ project_id: projectId });
    if (uploadCount === 0) {
      throw new BadRequestException('Project must have at least one upload');
    }
  }

  if (newStatus === 'approved') {
    // All uploads must be reviewed
    const needsReview = await uploadRepo.count({
      project_id: projectId,
      'parse_metadata.requires_review': true
    });
    if (needsReview > 0) {
      throw new BadRequestException(
        `${needsReview} uploads still require review`
      );
    }
  }

  if (newStatus === 'finalized') {
    // Generate report
    await reportService.generateProjectReport(projectId);
  }

  await project.update({ status: newStatus, updated_at: new Date() });
}
```

## Benefits of Phase 4.1

### For Consultants

- **Project-based organization**: Group related uploads
- **Workflow tracking**: Clear status progression
- **Review queue**: Prioritize low-confidence mappings
- **Notes system**: Document issues and recommendations
- **Audit trail**: Track all manual corrections

### For System

- **Adaptive parsing**: No hardcoded carrier mappings
- **Self-improving**: Template learning reduces LLM calls
- **Transparency**: Confidence scores guide review priority
- **Multi-tenant**: Isolated mappings per tenant
- **Cost-effective**: Cached templates minimize API costs

### For Users

- **Better accuracy**: Human review for ambiguous cases
- **Faster onboarding**: Zero configuration for new carriers
- **Quality feedback**: Consultant notes highlight data issues
- **Historical tracking**: Project-based analysis over time

## Migration from Phase 1-3

Existing uploads can be migrated to projects:

```sql
-- Create default project for each tenant
INSERT INTO project (tenant_id, name, status, period_start, period_end)
SELECT DISTINCT
  tenant_id,
  'Legacy Uploads (Pre-Phase 4.1)',
  'finalized',
  MIN(created_at),
  MAX(created_at)
FROM upload
WHERE project_id IS NULL
GROUP BY tenant_id;

-- Link existing uploads to default project
UPDATE upload
SET project_id = (
  SELECT id FROM project
  WHERE project.tenant_id = upload.tenant_id
    AND name = 'Legacy Uploads (Pre-Phase 4.1)'
)
WHERE project_id IS NULL;
```

## Performance Considerations

### LLM Call Optimization

1. **Template caching**: Check template database before LLM call
2. **Batch processing**: Group similar files for single LLM call
3. **Confidence thresholds**: Only invoke LLM for new patterns
4. **Async processing**: LLM calls in background workers

**Typical LLM usage:**
- New tenant, first upload: 1-2 LLM calls
- Same file structure: 0 LLM calls (template match)
- New carrier format: 1 LLM call
- Monthly cost: ~$5-10 per tenant (based on Claude API pricing)

### Database Queries

All queries respect RLS tenant isolation:

```typescript
// Set tenant context
await db.query('SET LOCAL app.current_tenant = $1', [tenantId]);

// Query projects (automatically filtered by tenant_id via RLS)
const projects = await projectRepo.find({
  status: 'in_review'
});
```

## References

- LLM Parser Service: `backend/src/modules/parsing/parsers/llm-parser.service.ts`
- Manual Mapping Service: `backend/src/modules/parsing/services/manual-mapping.service.ts`
- Template Service: `backend/src/modules/parsing/services/template.service.ts`
- Project Module: `backend/src/modules/project/`
- Migration 003: `backend/src/database/migrations/003_phase_4_1_project_workflow.ts`

---

**Last Updated:** 2025-10-02
**Version:** 1.0 (Phase 4.1)
