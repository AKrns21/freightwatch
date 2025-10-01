# FreightWatch: Refactoring mit Wiederverwendung
## Keine Parallelsysteme - Klarer Neuaufbau auf Bestehendem

**Erstellt**: 2025-01-07  
**Strategie**: Reuse where possible, Replace where necessary, Build what's missing

---

## Analysis: Was existiert bereits?

### ✅ Wiederverwendbar (Keep & Extend)

**Datenbank-Tabellen:**
- `tenant`, `carrier` → **Unverändert beibehalten**
- `upload` → **Erweitern** mit Project-Feldern
- `shipment` → **Erweitern** mit Completeness-Tracking
- `shipment_benchmark` → **Unverändert beibehalten**
- `tariff_zone_map`, `tariff_table`, `tariff_rate` → **Unverändert beibehalten**
- `diesel_floater`, `fx_rate` → **Unverändert beibehalten**
- `invoice_header`, `invoice_line` → **Unverändert beibehalten**

**Services:**
- `DatabaseService` (RLS) → **Wiederverwendbar**
- `TariffEngineService` (Benchmark-Berechnung) → **Core Logic wiederverwendbar**
- `UploadService` (File Storage) → **Storage-Teil wiederverwendbar**

**Infrastructure:**
- Bull Queue Setup → **Wiederverwendbar**
- TypeORM Setup → **Wiederverwendbar**
- RLS Policies → **Wiederverwendbar**

### ❌ Ersetzen (Replace)

**Tabellen die weg müssen:**
- `service_catalog`, `service_alias` → **Ersetzen durch simple Enum**
- `surcharge_catalog` → **Ersetzen durch Parser-Logik**
- `tariff_rule` → **Ersetzen durch Carrier.conversion_rules (JSONB)**

**Services die neu geschrieben werden:**
- `UploadProcessor` → **Komplett neu** (LLM-Integration)
- `ParsingModule` → **Erweitern** mit LLM + Templates

### 🆕 Komplett neu entwickeln

- `ProjectModule` (Workspace-Management)
- `LlmParserService` (AI-Parsing)
- `ReportModule` (Report-Versioning)
- `ConsultantNoteModule` (Annotations)
- Review-Workflow APIs
- Frontend (komplett)

---

## Refactoring-Plan: 4 Phasen

### Phase 1: Datenbank umbauen (Woche 1-2)

#### Migration 1: Tabellen ersetzen

```sql
-- migration: 001_refactor_to_project_workflow.sql

-- ============================================
-- STEP 1: Neue Tabellen hinzufügen
-- ============================================

CREATE TABLE project (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  name VARCHAR(255) NOT NULL,
  customer_name VARCHAR(255),
  phase VARCHAR(50) DEFAULT 'quick_check',
  status VARCHAR(50) DEFAULT 'draft',
  consultant_id UUID,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE project ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE consultant_note (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES project(id),
  note_type VARCHAR(50) NOT NULL,
  content TEXT NOT NULL,
  related_to_upload_id UUID REFERENCES upload(id),
  related_to_shipment_id UUID REFERENCES shipment(id),
  priority VARCHAR(20),
  status VARCHAR(50) DEFAULT 'open',
  created_by UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

ALTER TABLE consultant_note ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON consultant_note
  USING (project_id IN (SELECT id FROM project WHERE tenant_id = current_setting('app.current_tenant')::UUID));

CREATE TABLE parsing_template (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenant(id),
  name VARCHAR(255) NOT NULL,
  description TEXT,
  file_type VARCHAR(50) NOT NULL,
  template_category VARCHAR(50),
  detection JSONB NOT NULL,
  mappings JSONB NOT NULL,
  source VARCHAR(50) DEFAULT 'manual',
  verified_by UUID,
  verified_at TIMESTAMPTZ,
  usage_count INTEGER DEFAULT 0,
  last_used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE parsing_template ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON parsing_template
  USING (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE manual_mapping (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  upload_id UUID NOT NULL REFERENCES upload(id),
  field_name VARCHAR(100) NOT NULL,
  source_column VARCHAR(100),
  mapping_rule JSONB,
  confidence DECIMAL(3,2),
  notes TEXT,
  created_by UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE manual_mapping ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON manual_mapping
  USING (upload_id IN (SELECT id FROM upload WHERE tenant_id = current_setting('app.current_tenant')::UUID));

CREATE TABLE report (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES project(id),
  version INTEGER NOT NULL,
  report_type VARCHAR(50) NOT NULL,
  title VARCHAR(255),
  data_snapshot JSONB NOT NULL,
  data_completeness DECIMAL(3,2),
  shipment_count INTEGER,
  date_range_start DATE,
  date_range_end DATE,
  generated_by UUID NOT NULL,
  generated_at TIMESTAMPTZ DEFAULT now(),
  notes TEXT,
  UNIQUE(project_id, version)
);

ALTER TABLE report ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON report
  USING (project_id IN (SELECT id FROM project WHERE tenant_id = current_setting('app.current_tenant')::UUID));

-- ============================================
-- STEP 2: Bestehende Tabellen erweitern
-- ============================================

-- Upload wird project-based
ALTER TABLE upload ADD COLUMN project_id UUID REFERENCES project(id);
ALTER TABLE upload ADD COLUMN parse_method VARCHAR(50);
ALTER TABLE upload ADD COLUMN confidence DECIMAL(3,2);
ALTER TABLE upload ADD COLUMN suggested_mappings JSONB;
ALTER TABLE upload ADD COLUMN llm_analysis JSONB;
ALTER TABLE upload ADD COLUMN reviewed_by UUID;
ALTER TABLE upload ADD COLUMN reviewed_at TIMESTAMPTZ;
ALTER TABLE upload ADD COLUMN parsing_issues JSONB[];

-- Shipment wird quality-aware
ALTER TABLE shipment ADD COLUMN project_id UUID REFERENCES project(id);
ALTER TABLE shipment ADD COLUMN completeness_score DECIMAL(3,2);
ALTER TABLE shipment ADD COLUMN missing_fields TEXT[];
ALTER TABLE shipment ADD COLUMN data_quality_issues JSONB;
ALTER TABLE shipment ADD COLUMN consultant_notes TEXT;
ALTER TABLE shipment ADD COLUMN manual_override BOOLEAN DEFAULT FALSE;

-- Carrier bekommt Conversion-Rules
ALTER TABLE carrier ADD COLUMN conversion_rules JSONB;

-- ============================================
-- STEP 3: Service-Mapping vereinfachen
-- ============================================

-- service_level wird simple enum
ALTER TABLE shipment ALTER COLUMN service_level TYPE VARCHAR(20);
UPDATE shipment SET service_level = 'STANDARD' WHERE service_level IS NULL;

-- ============================================
-- STEP 4: Alte Tabellen droppen
-- ============================================

DROP TABLE IF EXISTS service_alias CASCADE;
DROP TABLE IF EXISTS service_catalog CASCADE;
DROP TABLE IF EXISTS surcharge_catalog CASCADE;
DROP TABLE IF EXISTS tariff_rule CASCADE;

-- ============================================
-- STEP 5: Indizes
-- ============================================

CREATE INDEX idx_upload_project ON upload(project_id);
CREATE INDEX idx_shipment_project ON shipment(project_id);
CREATE INDEX idx_project_tenant ON project(tenant_id);
CREATE INDEX idx_project_consultant ON project(consultant_id);
CREATE INDEX idx_template_category ON parsing_template(template_category);
CREATE INDEX idx_report_project ON report(project_id);
```

**Das war's. Eine Migration, die alles umbaut.**

---

### Phase 2: Backend-Services neu strukturieren (Wochen 3-10)

#### 2.1 ProjectModule (NEU)

```typescript
// src/modules/project/project.module.ts
import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Project } from './entities/project.entity';
import { ConsultantNote } from './entities/consultant-note.entity';
import { ProjectService } from './project.service';
import { ProjectController } from './project.controller';

@Module({
  imports: [TypeOrmModule.forFeature([Project, ConsultantNote])],
  providers: [ProjectService],
  controllers: [ProjectController],
  exports: [ProjectService],
})
export class ProjectModule {}
```

```typescript
// src/modules/project/project.service.ts
@Injectable()
export class ProjectService {
  constructor(
    @InjectRepository(Project)
    private readonly projectRepo: Repository<Project>,
    @InjectRepository(ConsultantNote)
    private readonly noteRepo: Repository<ConsultantNote>,
  ) {}

  async create(tenantId: string, data: CreateProjectDto): Promise<Project> {
    return this.projectRepo.save({
      ...data,
      tenant_id: tenantId,
    });
  }

  async findAll(tenantId: string): Promise<Project[]> {
    return this.projectRepo.find({
      where: { tenant_id: tenantId },
      order: { created_at: 'DESC' }
    });
  }

  async findOne(id: string, tenantId: string): Promise<Project> {
    return this.projectRepo.findOne({
      where: { id, tenant_id: tenantId },
      relations: ['uploads']
    });
  }

  async addNote(
    projectId: string,
    tenantId: string,
    note: CreateNoteDto,
    userId: string
  ): Promise<ConsultantNote> {
    return this.noteRepo.save({
      project_id: projectId,
      ...note,
      created_by: userId
    });
  }
}
```

#### 2.2 UploadService umbauen (REWRITE)

```typescript
// src/modules/upload/upload.service.ts (REWRITE - kein Legacy)
@Injectable()
export class UploadService {
  constructor(
    @InjectRepository(Upload)
    private readonly uploadRepo: Repository<Upload>,
    @Inject('UPLOAD_QUEUE') private readonly uploadQueue: Queue,
    private readonly storageService: StorageService,
    private readonly projectService: ProjectService,
  ) {}

  // Nur noch project-basierte Uploads
  async create(
    projectId: string,
    tenantId: string,
    file: Express.Multer.File
  ): Promise<Upload> {
    
    // Verify project exists
    const project = await this.projectService.findOne(projectId, tenantId);
    if (!project) {
      throw new NotFoundException('Project not found');
    }

    // Save file
    const storageUrl = await this.storageService.save(file);

    // Create upload record
    const upload = await this.uploadRepo.save({
      tenant_id: tenantId,
      project_id: projectId,
      file_name: file.originalname,
      mime_type: file.mimetype,
      file_size: file.size,
      storage_url: storageUrl,
      status: 'pending'
    });

    // Queue for processing
    await this.uploadQueue.add('parse-file', {
      uploadId: upload.id,
      projectId,
      tenantId
    });

    return upload;
  }

  async getPreview(uploadId: string, tenantId: string, limit: number = 50): Promise<any> {
    const upload = await this.uploadRepo.findOne({
      where: { id: uploadId, tenant_id: tenantId }
    });
    
    if (!upload) {
      throw new NotFoundException('Upload not found');
    }

    const fileBuffer = await this.storageService.load(upload.storage_url);
    
    // Parse first N rows
    if (upload.mime_type.includes('csv')) {
      return this.parseCsvPreview(fileBuffer, limit);
    } else if (upload.mime_type.includes('excel')) {
      return this.parseExcelPreview(fileBuffer, limit);
    }
    
    throw new BadRequestException('Unsupported file type for preview');
  }
}
```

#### 2.3 LlmParserService (NEU)

```typescript
// src/modules/parsing/llm-parser.service.ts
import { Injectable, Logger } from '@nestjs/common';
import Anthropic from '@anthropic-ai/sdk';

@Injectable()
export class LlmParserService {
  private readonly logger = new Logger(LlmParserService.name);
  private anthropic: Anthropic;

  constructor() {
    this.anthropic = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY,
    });
  }

  async analyzeUnknownFile(
    fileBuffer: Buffer,
    fileName: string,
    mimeType: string
  ): Promise<LlmParseResult> {
    
    this.logger.log(`Analyzing ${fileName} with LLM`);

    const content = await this.extractContent(fileBuffer, mimeType);
    const prompt = this.buildAnalysisPrompt(content, fileName);

    const response = await this.anthropic.messages.create({
      model: 'claude-sonnet-4-20250514',
      max_tokens: 4000,
      temperature: 0,
      messages: [{
        role: 'user',
        content: prompt
      }]
    });

    const textContent = response.content.find(c => c.type === 'text');
    if (!textContent || textContent.type !== 'text') {
      throw new Error('No text response from LLM');
    }

    const jsonMatch = textContent.text.match(/```json\n([\s\S]*?)\n```/);
    const jsonText = jsonMatch ? jsonMatch[1] : textContent.text;
    const analysis = JSON.parse(jsonText);

    return {
      file_type: analysis.file_type || 'unknown',
      confidence: analysis.confidence || 0,
      description: analysis.description || '',
      column_mappings: analysis.column_mappings || [],
      tariff_structure: analysis.tariff_structure,
      issues: analysis.issues || [],
      suggested_actions: analysis.suggested_actions || [],
      needs_review: analysis.confidence < 0.85 || analysis.issues.length > 0
    };
  }

  private async extractContent(buffer: Buffer, mimeType: string): Promise<string> {
    if (mimeType.includes('csv') || mimeType.includes('text')) {
      return buffer.toString('utf-8');
    }

    if (mimeType.includes('excel') || mimeType.includes('spreadsheet')) {
      const XLSX = require('xlsx');
      const workbook = XLSX.read(buffer);
      const sheet = workbook.Sheets[workbook.SheetNames[0]];
      return XLSX.utils.sheet_to_csv(sheet);
    }

    if (mimeType.includes('pdf')) {
      const pdfParse = require('pdf-parse');
      const data = await pdfParse(buffer);
      return data.text;
    }

    return buffer.toString('utf-8').substring(0, 5000);
  }

  private buildAnalysisPrompt(content: string, fileName: string): string {
    return `You are analyzing a freight/logistics data file.

File: ${fileName}
Content (first 2000 chars):
${content.substring(0, 2000)}

Analyze and return JSON:
{
  "file_type": "shipment_list" | "invoice" | "tariff_table" | "route_documentation" | "unknown",
  "confidence": 0.0-1.0,
  "description": "Brief description",
  "column_mappings": [
    {
      "column": "Column name or letter",
      "field": "origin_zip | dest_zip | weight_kg | carrier_name | date | actual_cost | etc",
      "confidence": 0.0-1.0,
      "pattern": "Transformation needed",
      "sample_values": ["val1", "val2", "val3"]
    }
  ],
  "issues": ["Data quality issues"],
  "suggested_actions": ["Actions for consultant"]
}

Be conservative - only suggest mappings with confidence >= 0.7.`;
  }
}
```

#### 2.4 UploadProcessor (REWRITE mit Hybrid-Logik)

```typescript
// src/modules/upload/upload-processor.service.ts (REWRITE)
import { Processor, Process } from '@nestjs/bull';
import { Injectable, Logger } from '@nestjs/common';
import { Job } from 'bull';
import { LlmParserService } from '../parsing/llm-parser.service';
import { TemplateMatcherService } from '../parsing/template-matcher.service';
import { TariffPdfParserService } from '../tariff/tariff-pdf-parser.service';
import { InvoiceParserService } from '../invoice/invoice-parser.service';

@Processor('upload')
@Injectable()
export class UploadProcessor {
  private readonly logger = new Logger(UploadProcessor.name);

  constructor(
    @InjectRepository(Upload)
    private readonly uploadRepo: Repository<Upload>,
    private readonly templateMatcher: TemplateMatcherService,
    private readonly llmParser: LlmParserService,
    private readonly tariffParser: TariffPdfParserService,
    private readonly invoiceParser: InvoiceParserService,
    private readonly storageService: StorageService,
  ) {}

  @Process('parse-file')
  async handleParseFile(job: Job): Promise<void> {
    const { uploadId, projectId, tenantId } = job.data;

    const upload = await this.uploadRepo.findOne({
      where: { id: uploadId, tenant_id: tenantId }
    });

    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    try {
      const fileBuffer = await this.storageService.load(upload.storage_url);

      // STEP 1: Check for template match
      const templateMatch = await this.templateMatcher.findMatch(upload);

      if (templateMatch && templateMatch.confidence > 0.9) {
        this.logger.log(`Template match: ${templateMatch.template.name}`);
        
        // Route to appropriate parser based on template category
        await this.parseWithTemplate(upload, templateMatch.template, fileBuffer, tenantId);
        
        await this.uploadRepo.update(uploadId, {
          status: 'parsed',
          parse_method: 'template',
          confidence: templateMatch.confidence
        });
        
        return;
      }

      // STEP 2: Unknown format → LLM
      this.logger.log('Unknown format, using LLM');
      
      const llmResult = await this.llmParser.analyzeUnknownFile(
        fileBuffer,
        upload.file_name,
        upload.mime_type
      );

      // Save LLM analysis for review
      await this.uploadRepo.update(uploadId, {
        status: 'needs_review',
        parse_method: 'llm',
        confidence: llmResult.confidence,
        suggested_mappings: llmResult.column_mappings,
        llm_analysis: llmResult,
        parsing_issues: llmResult.issues
      });

      this.logger.log(`LLM analysis complete, needs review: ${llmResult.needs_review}`);

    } catch (error) {
      this.logger.error(`Error processing upload ${uploadId}:`, error);
      
      await this.uploadRepo.update(uploadId, {
        status: 'error',
        parse_errors: { message: error.message, stack: error.stack }
      });
    }
  }

  private async parseWithTemplate(
    upload: Upload,
    template: ParsingTemplate,
    fileBuffer: Buffer,
    tenantId: string
  ): Promise<void> {
    
    switch (template.template_category) {
      case 'tariff':
        await this.tariffParser.parseCarrierTariff(fileBuffer, tenantId);
        break;
      
      case 'invoice':
        await this.invoiceParser.parseAndMatchInvoice(
          fileBuffer,
          upload.project_id,
          tenantId
        );
        break;
      
      case 'shipment_list':
        await this.parseShipmentList(fileBuffer, template, upload.project_id, tenantId);
        break;
      
      default:
        throw new Error(`Unknown template category: ${template.template_category}`);
    }
  }

  private async parseShipmentList(
    buffer: Buffer,
    template: ParsingTemplate,
    projectId: string,
    tenantId: string
  ): Promise<void> {
    // Use template mappings to parse CSV/Excel
    // Implementation depends on file type
  }
}
```

#### 2.5 TariffEngineService erweitern (REUSE Core Logic)

```typescript
// src/modules/tariff/tariff-engine.service.ts (EXTEND - Core bleibt)
@Injectable()
export class TariffEngineService {
  // EXISTING: Die Core-Logik bleibt identisch
  async calculateBenchmark(shipmentId: string, tenantId: string): Promise<void> {
    // Bestehende Implementierung - funktioniert weiterhin
  }

  // NEW: Wrapper mit Partial Data Support
  async calculateBenchmarkForProject(projectId: string, tenantId: string): Promise<void> {
    const shipments = await this.shipmentRepo.find({
      where: { project_id: projectId, tenant_id: tenantId }
    });

    this.logger.log(`Calculating benchmarks for ${shipments.length} shipments`);

    for (const shipment of shipments) {
      try {
        // Use existing logic
        await this.calculateBenchmark(shipment.id, tenantId);
        
        // Update completeness
        await this.updateCompleteness(shipment.id, 1.0, []);
        
      } catch (error) {
        this.logger.warn(`Partial benchmark for ${shipment.id}: ${error.message}`);
        
        // Mark as partial
        await this.updateCompleteness(
          shipment.id, 
          0.5, 
          this.identifyMissingFields(shipment)
        );
      }
    }
  }

  private async updateCompleteness(
    shipmentId: string,
    score: number,
    missingFields: string[]
  ): Promise<void> {
    await this.shipmentRepo.update(shipmentId, {
      completeness_score: score,
      missing_fields: missingFields
    });
  }

  private identifyMissingFields(shipment: Shipment): string[] {
    const required = ['origin_zip', 'dest_zip', 'weight_kg', 'carrier_id', 'date', 'actual_cost'];
    return required.filter(field => !shipment[field]);
  }
}
```

#### 2.6 ReportModule (NEU - baut auf TariffEngine auf)

```typescript
// src/modules/report/report.module.ts
@Module({
  imports: [
    TypeOrmModule.forFeature([Report]),
    TariffModule,
    ShipmentModule
  ],
  providers: [ReportService, ReportAggregationService],
  controllers: [ReportController],
})
export class ReportModule {}
```

```typescript
// src/modules/report/report.service.ts
@Injectable()
export class ReportService {
  constructor(
    @InjectRepository(Report)
    private readonly reportRepo: Repository<Report>,
    private readonly aggregationService: ReportAggregationService,
  ) {}

  async generate(
    projectId: string,
    reportType: 'quick_check' | 'deep_dive' | 'final',
    tenantId: string
  ): Promise<Report> {
    
    const shipments = await this.shipmentRepo.find({
      where: { project_id: projectId, tenant_id: tenantId },
      relations: ['benchmark']
    });

    const completeness = shipments.reduce((sum, s) => 
      sum + (s.completeness_score || 0), 0
    ) / shipments.length;

    const dataSnapshot = {
      summary: await this.calculateSummary(shipments),
      carriers: await this.aggregationService.aggregateByCarrier(projectId),
      zones: await this.aggregationService.aggregateByZone(projectId),
      quick_wins: await this.aggregationService.identifyQuickWins(projectId)
    };

    const lastReport = await this.reportRepo.findOne({
      where: { project_id: projectId },
      order: { version: 'DESC' }
    });

    const version = (lastReport?.version || 0) + 1;

    return this.reportRepo.save({
      project_id: projectId,
      version,
      report_type: reportType,
      title: `${reportType} Report v${version}`,
      data_snapshot: dataSnapshot,
      data_completeness: completeness,
      shipment_count: shipments.length,
      generated_by: tenantId
    });
  }
}
```

---

### Phase 3: Domain-Specific Parsers (Wochen 11-16)

Diese Services sind **NEU**, weil sie im aktuellen System fehlen:

#### 3.1 TariffPdfParserService (NEU)

```typescript
// src/modules/tariff/tariff-pdf-parser.service.ts
// Implementation aus Refactoring Guide v3.1, Phase 2.5.1
// Template-basierter Parser für strukturierte Carrier-PDFs
```

#### 3.2 InvoiceParserService (NEU)

```typescript
// src/modules/invoice/invoice-parser.service.ts
// Implementation aus Refactoring Guide v3.1, Phase 2.5.2
// Carrier-spezifische Invoice-Parser + LLM-Fallback
```

#### 3.3 InvoiceMatcherService (NEU)

```typescript
// src/modules/invoice/invoice-matcher.service.ts
// Implementation aus Refactoring Guide v3.1, Phase 2.5.3
// Matching von Invoice-Lines zu Shipments
```

#### 3.4 ReportAggregationService (NEU)

```typescript
// src/modules/report/report-aggregation.service.ts
// Implementation aus Refactoring Guide v3.1, Phase 2.5.5
// SQL-basierte Aggregationen für Reports
```

**Diese Services nutzen die existierende Datenbank-Struktur**, sind aber neue Business-Logik.

---

### Phase 4: Review-Workflow & Frontend (Wochen 17-24)

#### 4.1 Review-APIs (NEU)

```typescript
// src/modules/upload/upload-review.controller.ts
@Controller('api/uploads/:uploadId/review')
export class UploadReviewController {
  
  @Get()
  async getReviewData(
    @Param('uploadId') uploadId: string,
    @TenantId() tenantId: string
  ) {
    const upload = await this.uploadService.findOne(uploadId, tenantId);
    const preview = await this.uploadService.getPreview(uploadId, tenantId, 50);
    
    return {
      upload,
      llm_analysis: upload.llm_analysis,
      suggested_mappings: upload.suggested_mappings,
      preview,
      issues: upload.parsing_issues
    };
  }

  @Post('accept-mappings')
  async acceptMappings(
    @Param('uploadId') uploadId: string,
    @TenantId() tenantId: string,
    @Body() body: { mappings: any[]; save_as_template?: boolean }
  ) {
    await this.uploadService.applyMappings(uploadId, tenantId, body.mappings);
    
    if (body.save_as_template) {
      await this.templateService.createFromMappings(uploadId, tenantId, body.mappings);
    }
    
    return { success: true };
  }
}
```

#### 4.2 Frontend (NEU - komplett)

- Project-Overview
- Upload-Review-UI
- Report-Viewer
- Consultant-Dashboard

---

## Was wird wiederverwendet vs. neu?

### ✅ Wiederverwendet (70% der Datenbank, 40% der Services)

**Datenbank:**
- `tenant`, `carrier`, `shipment`, `shipment_benchmark`
- `tariff_zone_map`, `tariff_table`, `tariff_rate`
- `diesel_floater`, `fx_rate`
- `invoice_header`, `invoice_line`

**Services:**
- `DatabaseService` (100% wiederverwendbar)
- `TariffEngineService.calculateBenchmark()` (Core-Logic 100% wiederverwendbar)
- `StorageService` (100% wiederverwendbar)
- RLS-Policies (100% wiederverwendbar)

### ❌ Ersetzt (4 Tabellen, 2 Services)

**Tabellen:**
- `service_catalog`, `service_alias` → Enum
- `surcharge_catalog` → Parser-Logic
- `tariff_rule` → JSONB

**Services:**
- `UploadProcessor` (komplett neu wegen LLM)
- `ParsingModule` (erweitert mit Templates)

### 🆕 Neu entwickelt (6 Tabellen, 8 Services)

**Tabellen:**
- `project`, `consultant_note`, `parsing_template`
- `manual_mapping`, `report`

**Services:**
- `ProjectModule`
- `LlmParserService`
- `TariffPdfParserService`
- `InvoiceParserService`
- `InvoiceMatcherService`
- `ReportModule`
- `ReportAggregationService`
- Review-APIs

---

## Implementierungs-Reihenfolge

### Woche 1-2: Datenbank
- [ ] Run migration `001_refactor_to_project_workflow.sql`
- [ ] Verify: Alte Tabellen weg, neue da
- [ ] Test: RLS funktioniert mit neuen Tabellen

### Woche 3-4: Project-System
- [ ] Implement `ProjectModule`
- [ ] Update `Upload` entity
- [ ] Test: Create project → upload files

### Woche 5-6: LLM-Integration
- [ ] Install `@anthropic-ai/sdk`
- [ ] Implement `LlmParserService`
- [ ] Test: Unknown CSV → LLM analysis

### Woche 7-8: UploadProcessor neu
- [ ] Rewrite `UploadProcessor` mit Hybrid-Logic
- [ ] Implement `TemplateMatcherService`
- [ ] Test: Template-Match → Auto-Parse

### Woche 9-10: TariffEngine erweitern
- [ ] Add `calculateBenchmarkForProject()`
- [ ] Add completeness tracking
- [ ] Test: Partial data → partial benchmark

### Woche 11-12: Domain Parsers (Tariff)
- [ ] Implement `TariffPdfParserService`
- [ ] Add 3 carrier templates
- [ ] Test: Gebr. Weiss PDF → 240 rates

### Woche 13-14: Domain Parsers (Invoice)
- [ ] Implement `InvoiceParserService`
- [ ] Implement `InvoiceMatcherService`
- [ ] Test: COSI invoices → matched shipments

### Woche 15-16: Report-System
- [ ] Implement `ReportModule`
- [ ] Implement `ReportAggregationService`
- [ ] Test: Generate v1 → v2 reports

### Woche 17-18: Review-APIs
- [ ] Implement `UploadReviewController`
- [ ] Add preview endpoints
- [ ] Test: Review workflow end-to-end

### Woche 19-20: Frontend
- [ ] Build project overview
- [ ] Build upload review UI
- [ ] Build report viewer

### Woche 21-22: Integration Testing
- [ ] End-to-end test with MECU data
- [ ] Performance testing
- [ ] Bug fixes

### Woche 23-24: Production
- [ ] Deploy to staging
- [ ] Internal testing
- [ ] Production release

---

## Success Metrics

**Nach Woche 8:**
- [ ] LLM kann Files analysieren
- [ ] Template-Matching funktioniert
- [ ] Uploads zu Projects

**Nach Woche 16:**
- [ ] Tarif-PDFs → Rates importiert
- [ ] Invoice-PDFs → Shipments matched
- [ ] Benchmark berechnet
- [ ] Reports generiert

**Nach Woche 24:**
- [ ] Full workflow: Upload → Review → Report
- [ ] Frontend funktional
- [ ] Produktionsreif

---

## Was NICHT passiert

- ❌ Kein Legacy-Support
- ❌ Keine Feature-Flags
- ❌ Keine Dual-Mode
- ❌ Keine Backwards-Compatibility
- ❌ Keine graduellen Rollouts

**Ein System. Ein Workflow. Clean.**