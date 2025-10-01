# FreightWatch: Refactoring MVP v3
## Vollständiger Implementierungsplan mit allen notwendigen Änderungen

**Erstellt**: 2025-01-07  
**Status**: Current Implementation Analysis + Action Plan  
**Ziel**: Project-basierter Workflow mit LLM-Unterstützung

---

## Executive Summary

**Gute Nachricht:** 70% der Backend-Services sind bereits implementiert.  
**Schlechte Nachricht:** Migration wurde nicht ausgeführt, kritische Inkonsistenzen zwischen Code und DB-Schema.

**Hauptproblem:** Die neuen Services existieren bereits, aber die alte Infrastruktur (service_alias, service_catalog, tariff_rule) wird parallel noch genutzt. Das muss aufgelöst werden.

---

## Current State Analysis

### ✅ Bereits vorhanden

**Datenbank:**
- Migration `003_refactor_to_project_workflow.sql` definiert
- Neue Tabellen-Schemas: project, consultant_note, parsing_template, manual_mapping, report

**Backend-Services (70% komplett):**
- ProjectModule mit Entities
- LlmParserService
- TemplateMatcherService
- TariffPdfParserService
- InvoiceParserService
- InvoiceMatcherService  
- ReportModule + ReportAggregationService
- InvoiceController

### ⚠️ Kritische Inkonsistenzen

1. **Migration nicht ausgeführt:**
   - Alte Tabellen (service_alias, service_catalog, surcharge_catalog, tariff_rule) existieren noch
   - Neue Tabellen (project, consultant_note, parsing_template, manual_mapping, report) existieren nicht
   
2. **ServiceMapperService nutzt alte Struktur:**
   - Verwendet `serviceAliasRepository` 
   - Sollte laut Plan nur Enums nutzen
   
3. **Upload/Shipment Entities nicht erweitert:**
   - Fehlende Felder für Project-Workflow
   - Fehlende Completeness-Tracking Felder

4. **UploadProcessor nicht refactored:**
   - Nutzt alte Logik ohne Template-Matching
   - Kein LLM-Fallback implementiert

### ❌ Komplett fehlend

- UploadReviewController
- Frontend (0%)
- Integration Tests für neuen Workflow
- Carrier.conversion_rules Migration

---

## Phase 1: Datenbank-Konsistenz herstellen (Woche 1)

### 1.1 Migration Status prüfen und ausführen

```bash
# Check welche Migrations gelaufen sind
npm run typeorm migration:show

# Falls 003_refactor_to_project_workflow nicht gelaufen:
npm run typeorm migration:run

# Verify neue Tabellen existieren
psql -d freightwatch -c "\dt project"
psql -d freightwatch -c "\dt consultant_note"
psql -d freightwatch -c "\dt parsing_template"

# Verify alte Tabellen weg sind
psql -d freightwatch -c "\dt service_alias"  # sollte nicht existieren
psql -d freightwatch -c "\dt service_catalog" # sollte nicht existieren
```

**Falls Migration fehlschlägt:**
- Backup erstellen
- Migration manuell Schritt für Schritt ausführen
- Insbesondere CHECK: Gibt es noch Referenzen auf service_alias/service_catalog?

### 1.2 Entity-Updates durchführen

**A) Upload Entity erweitern**

```typescript
// backend/src/modules/upload/entities/upload.entity.ts
// HINZUFÜGEN:

@Column({ type: 'uuid', nullable: true })
project_id: string;

@Column({ type: 'jsonb', nullable: true })
llm_analysis: {
  file_type: string;
  confidence: number;
  description: string;
  column_mappings: any[];
  issues: string[];
  suggested_actions: string[];
};

@Column({ type: 'jsonb', nullable: true })
suggested_mappings: any[];

@Column({ type: 'uuid', nullable: true })
reviewed_by: string;

@Column({ type: 'timestamptz', nullable: true })
reviewed_at: Date;

@Column({ type: 'jsonb', nullable: true })
parsing_issues: any[];

@Column({ default: 'template' })
parse_method: 'template' | 'llm' | 'heuristic' | 'manual';

@Column({ type: 'decimal', precision: 3, scale: 2, nullable: true })
confidence: number;
```

**B) Shipment Entity erweitern**

```typescript
// backend/src/modules/parsing/entities/shipment.entity.ts
// HINZUFÜGEN:

@Column({ type: 'uuid', nullable: true })
project_id: string;

@Column({ type: 'decimal', precision: 3, scale: 2, nullable: true })
completeness_score: number;

@Column({ type: 'text', array: true, nullable: true })
missing_fields: string[];

@Column({ type: 'jsonb', nullable: true })
data_quality_issues: any[];

@Column({ type: 'text', nullable: true })
consultant_notes: string;

@Column({ default: false })
manual_override: boolean;
```

**C) Carrier Entity erweitern**

```typescript
// backend/src/modules/carrier/entities/carrier.entity.ts
// HINZUFÜGEN:

@Column({ type: 'jsonb', default: {} })
conversion_rules: {
  ldm_to_kg?: number;
  min_pallet_weight_kg?: number;
  length_surcharge?: { length_over_m: number; surcharge_amount: number }[];
  [key: string]: any;
};
```

### 1.3 Migration für bestehende Daten erstellen

```bash
npm run typeorm migration:generate -- -n AddProjectWorkflowFields
```

Dann manuell anpassen falls nötig:

```sql
-- backend/src/database/migrations/XXX_add_project_workflow_fields.sql

-- Add new columns to upload
ALTER TABLE upload ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES project(id);
ALTER TABLE upload ADD COLUMN IF NOT EXISTS llm_analysis JSONB;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS suggested_mappings JSONB;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS reviewed_by UUID;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS parsing_issues JSONB;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS parse_method VARCHAR(20) DEFAULT 'template';
ALTER TABLE upload ADD COLUMN IF NOT EXISTS confidence DECIMAL(3,2);

-- Add new columns to shipment
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES project(id);
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS completeness_score DECIMAL(3,2);
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS missing_fields TEXT[];
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS data_quality_issues JSONB;
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS consultant_notes TEXT;
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS manual_override BOOLEAN DEFAULT FALSE;

-- Add conversion_rules to carrier
ALTER TABLE carrier ADD COLUMN IF NOT EXISTS conversion_rules JSONB DEFAULT '{}'::jsonb;

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_upload_project ON upload(project_id);
CREATE INDEX IF NOT EXISTS idx_shipment_project ON shipment(project_id);
```

---

## Phase 2: Service-Refactoring (Woche 2)

### 2.1 ServiceMapperService vereinfachen

**PROBLEM:** ServiceMapperService nutzt noch `serviceAliasRepository`, aber diese Tabelle wird gedroppt.

**LÖSUNG:** Vereinfachen auf Enum-only Matching mit Fuzzy-Fallback.

```typescript
// backend/src/modules/parsing/service-mapper.service.ts
// KOMPLETT ERSETZEN durch simple Version:

import { Injectable, Logger } from '@nestjs/common';

@Injectable()
export class ServiceMapperService {
  private readonly logger = new Logger(ServiceMapperService.name);

  /**
   * Normalize service text to standard service code
   * NO DATABASE LOOKUPS - pure fuzzy matching
   */
  async normalize(serviceText: string): Promise<string> {
    if (!serviceText) {
      return 'STANDARD';
    }

    const normalized = serviceText.toLowerCase().trim();

    // Express patterns
    if (/express|24h|next.*day|overnight|eilsendung|schnell/i.test(normalized)) {
      return 'EXPRESS';
    }

    // Same Day
    if (/same.*day|sameday/i.test(normalized)) {
      return 'SAME_DAY';
    }

    // Economy
    if (/eco|economy|slow|spar|günstig|cheap|sparversand|langsam/i.test(normalized)) {
      return 'ECONOMY';
    }

    // Premium
    if (/premium|priority|first.*class|firstclass/i.test(normalized)) {
      return 'PREMIUM';
    }

    // Standard (default)
    return 'STANDARD';
  }

  /**
   * Bulk normalize - process multiple service texts at once
   */
  async bulkNormalize(serviceTexts: string[]): Promise<Map<string, string>> {
    const results = new Map<string, string>();

    for (const text of serviceTexts) {
      if (!results.has(text)) {
        const normalized = await this.normalize(text);
        results.set(text, normalized);
      }
    }

    return results;
  }
}
```

**Tests anpassen:**

```typescript
// backend/src/modules/parsing/service-mapper.service.spec.ts
// ENTFERNEN: Alle Tests die serviceAliasRepository mocken
// BEHALTEN: Fuzzy-Matching Tests

describe('ServiceMapperService', () => {
  let service: ServiceMapperService;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [ServiceMapperService],
    }).compile();

    service = module.get<ServiceMapperService>(ServiceMapperService);
  });

  describe('normalize', () => {
    it('should match express variants', async () => {
      expect(await service.normalize('Express Delivery')).toBe('EXPRESS');
      expect(await service.normalize('24h Service')).toBe('EXPRESS');
      expect(await service.normalize('Overnight')).toBe('EXPRESS');
    });

    it('should match economy variants', async () => {
      expect(await service.normalize('Economy')).toBe('ECONOMY');
      expect(await service.normalize('Eco Delivery')).toBe('ECONOMY');
      expect(await service.normalize('Sparversand')).toBe('ECONOMY');
    });

    it('should default to STANDARD', async () => {
      expect(await service.normalize('Unknown Service')).toBe('STANDARD');
      expect(await service.normalize('')).toBe('STANDARD');
    });
  });
});
```

### 2.2 ParsingModule bereinigen

```typescript
// backend/src/modules/parsing/parsing.module.ts
// ENTFERNEN:
import { ServiceCatalog } from './entities/service-catalog.entity';
import { ServiceAlias } from './entities/service-alias.entity';

// TypeOrmModule.forFeature([]) bereinigen:
@Module({
  imports: [
    TypeOrmModule.forFeature([
      Shipment,
      // ServiceCatalog, // LÖSCHEN
      // ServiceAlias, // LÖSCHEN
      ParsingTemplate,
      ManualMapping,
      Upload,
    ]),
  ],
  // ... rest bleibt
})
```

### 2.3 TariffEngineService anpassen

**PROBLEM:** TariffEngineService nutzt evtl. noch `tariff_rule` Tabelle.

**LÖSUNG:** Auf `carrier.conversion_rules` (JSONB) umstellen.

```typescript
// backend/src/modules/tariff/tariff-engine.service.ts
// ERSETZEN: Alle tariff_rule Lookups durch carrier.conversion_rules

// VORHER:
const rule = await this.tariffRuleRepo.findOne({
  where: { carrier_id: carrierId, rule_type: 'ldm_conversion' }
});
const ldmToKg = rule?.param_json.ldm_to_kg || 1850;

// NACHHER:
const carrier = await this.carrierRepo.findOne({
  where: { id: carrierId }
});
const ldmToKg = carrier?.conversion_rules?.ldm_to_kg || 1850;

// Wichtig: Warnung loggen wenn Fallback genutzt wird
if (!carrier?.conversion_rules?.ldm_to_kg) {
  this.logger.warn({
    event: 'ldm_conversion_fallback',
    carrier_id: carrierId,
    fallback_value: 1850
  });
}
```

---

## Phase 3: Upload-Processor Refactoring (Woche 3)

### 3.1 UploadProcessor umbauen auf Hybrid-Ansatz

```typescript
// backend/src/modules/upload/upload-processor.service.ts
// KOMPLETT NEU SCHREIBEN

import { Processor, Process } from '@nestjs/bull';
import { Injectable, Logger } from '@nestjs/common';
import { Job } from 'bull';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Upload } from './entities/upload.entity';
import { TemplateMatcherService } from '../parsing/services/template-matcher.service';
import { LlmParserService } from '../parsing/services/llm-parser.service';
import { CsvParserService } from '../parsing/csv-parser.service';

@Processor('upload')
@Injectable()
export class UploadProcessor {
  private readonly logger = new Logger(UploadProcessor.name);

  constructor(
    @InjectRepository(Upload)
    private readonly uploadRepo: Repository<Upload>,
    private readonly templateMatcher: TemplateMatcherService,
    private readonly llmParser: LlmParserService,
    private readonly csvParser: CsvParserService,
  ) {}

  @Process('parse-file')
  async handleParseFile(job: Job): Promise<void> {
    const { uploadId, tenantId } = job.data;

    this.logger.log({
      event: 'upload_processing_start',
      upload_id: uploadId,
      tenant_id: tenantId,
    });

    try {
      const upload = await this.uploadRepo.findOne({
        where: { id: uploadId, tenant_id: tenantId },
      });

      if (!upload) {
        throw new Error(`Upload ${uploadId} not found`);
      }

      // Step 1: Try Template Matching
      const template = await this.templateMatcher.findMatch(upload, tenantId);

      if (template && template.confidence >= 0.8) {
        this.logger.log({
          event: 'template_match_found',
          upload_id: uploadId,
          template_id: template.template.id,
          confidence: template.confidence,
        });

        // Parse with template
        await this.parseWithTemplate(upload, template.template);

        await this.uploadRepo.update(uploadId, {
          status: 'parsed',
          parse_method: 'template',
          confidence: template.confidence,
        });

        return;
      }

      // Step 2: Fall back to LLM analysis
      if (!this.llmParser.isAvailable()) {
        throw new Error('No template match and LLM not available');
      }

      this.logger.log({
        event: 'llm_analysis_start',
        upload_id: uploadId,
      });

      const llmResult = await this.llmParser.analyzeFile(
        upload.file_name,
        upload.mime_type,
        // Load file content here
      );

      await this.uploadRepo.update(uploadId, {
        status: 'needs_review',
        parse_method: 'llm',
        confidence: llmResult.confidence,
        llm_analysis: llmResult,
        suggested_mappings: llmResult.column_mappings,
        parsing_issues: llmResult.issues,
      });

      this.logger.log({
        event: 'llm_analysis_complete',
        upload_id: uploadId,
        confidence: llmResult.confidence,
        needs_review: llmResult.needs_review,
      });

    } catch (error) {
      this.logger.error({
        event: 'upload_processing_error',
        upload_id: uploadId,
        error: (error as Error).message,
      });

      await this.uploadRepo.update(uploadId, {
        status: 'error',
        parse_errors: { message: (error as Error).message },
      });
    }
  }

  private async parseWithTemplate(
    upload: Upload,
    template: ParsingTemplate,
  ): Promise<void> {
    // Implementierung abhängig von file_type
    if (upload.mime_type.includes('csv') || upload.mime_type.includes('excel')) {
      await this.csvParser.parseWithTemplate(upload, template);
    }
    // TODO: PDF, andere Formate
  }
}
```

### 3.2 CsvParserService erweitern

```typescript
// backend/src/modules/parsing/csv-parser.service.ts
// HINZUFÜGEN:

async parseWithTemplate(
  upload: Upload,
  template: ParsingTemplate,
): Promise<void> {
  // Load file content
  const fileBuffer = await this.storageService.getFile(upload.storage_url);
  
  // Parse with template mappings
  const mappings = template.mappings as any;
  
  // Use Papaparse with template-defined headers
  const parsed = Papa.parse(fileBuffer.toString('utf8'), {
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
  });

  const shipments = [];

  for (const row of parsed.data) {
    const shipment = {
      tenant_id: upload.tenant_id,
      upload_id: upload.id,
      project_id: upload.project_id,
      
      // Map fields using template
      date: this.extractField(row, mappings.date),
      carrier_name: this.extractField(row, mappings.carrier_name),
      origin_zip: this.extractField(row, mappings.origin_zip),
      dest_zip: this.extractField(row, mappings.dest_zip),
      weight_kg: this.extractField(row, mappings.weight_kg),
      actual_cost: this.extractField(row, mappings.actual_cost),
      // ... weitere Felder
    };

    // Calculate completeness
    shipment.completeness_score = this.calculateCompleteness(shipment);
    shipment.missing_fields = this.findMissingFields(shipment);

    shipments.push(shipment);
  }

  // Bulk insert
  await this.shipmentRepo.save(shipments);

  // Update template usage
  await this.templateRepo.increment(
    { id: template.id },
    'usage_count',
    1,
  );
  await this.templateRepo.update(template.id, {
    last_used_at: new Date(),
  });
}

private extractField(row: any, mapping: any): any {
  if (!mapping) return null;
  
  // Try keywords
  if (mapping.keywords) {
    for (const keyword of mapping.keywords) {
      if (row[keyword] !== undefined) {
        return row[keyword];
      }
    }
  }
  
  // Try column letter/index
  if (mapping.column) {
    return row[mapping.column];
  }
  
  return null;
}

private calculateCompleteness(shipment: any): number {
  const requiredFields = [
    'date', 'carrier_name', 'origin_zip', 'dest_zip', 
    'weight_kg', 'actual_cost'
  ];
  
  let score = 0;
  for (const field of requiredFields) {
    if (shipment[field]) {
      score += 1 / requiredFields.length;
    }
  }
  
  return Math.round(score * 100) / 100;
}

private findMissingFields(shipment: any): string[] {
  const requiredFields = [
    'date', 'carrier_name', 'origin_zip', 'dest_zip',
    'weight_kg', 'actual_cost'
  ];
  
  return requiredFields.filter(field => !shipment[field]);
}
```

---

## Phase 4: Review-Workflow APIs (Woche 4)

### 4.1 UploadReviewController erstellen

```typescript
// backend/src/modules/upload/upload-review.controller.ts
// NEU ERSTELLEN

import {
  Controller,
  Get,
  Post,
  Param,
  Body,
  UseGuards,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/guards/jwt-auth.guard';
import { TenantId } from '../auth/tenant.decorator';
import { UploadService } from './upload.service';
import { TemplateService } from '../parsing/template.service';

@Controller('uploads/:uploadId/review')
@UseGuards(JwtAuthGuard)
export class UploadReviewController {
  constructor(
    private readonly uploadService: UploadService,
    private readonly templateService: TemplateService,
  ) {}

  /**
   * Get review data for upload
   * GET /uploads/:uploadId/review
   */
  @Get()
  async getReviewData(
    @Param('uploadId') uploadId: string,
    @TenantId() tenantId: string,
  ) {
    const upload = await this.uploadService.findOne(uploadId, tenantId);
    const preview = await this.uploadService.getPreview(uploadId, tenantId, 50);

    return {
      upload: {
        id: upload.id,
        file_name: upload.file_name,
        status: upload.status,
        parse_method: upload.parse_method,
        confidence: upload.confidence,
      },
      llm_analysis: upload.llm_analysis,
      suggested_mappings: upload.suggested_mappings,
      preview,
      issues: upload.parsing_issues,
    };
  }

  /**
   * Accept suggested mappings
   * POST /uploads/:uploadId/review/accept
   */
  @Post('accept')
  async acceptMappings(
    @Param('uploadId') uploadId: string,
    @TenantId() tenantId: string,
    @Body()
    body: {
      mappings: any[];
      save_as_template?: boolean;
      template_name?: string;
    },
  ) {
    // Apply mappings and parse file
    await this.uploadService.applyMappings(uploadId, tenantId, body.mappings);

    // Optionally save as template
    if (body.save_as_template) {
      await this.templateService.createFromMappings(
        uploadId,
        tenantId,
        body.mappings,
        body.template_name,
      );
    }

    return { success: true };
  }

  /**
   * Reject and request manual mapping
   * POST /uploads/:uploadId/review/reject
   */
  @Post('reject')
  async rejectMappings(
    @Param('uploadId') uploadId: string,
    @TenantId() tenantId: string,
    @Body() body: { reason: string },
  ) {
    await this.uploadService.markForManualReview(uploadId, tenantId, body.reason);

    return { success: true };
  }

  /**
   * Update mapping for specific field
   * POST /uploads/:uploadId/review/update-mapping
   */
  @Post('update-mapping')
  async updateMapping(
    @Param('uploadId') uploadId: string,
    @TenantId() tenantId: string,
    @Body()
    body: {
      field_name: string;
      source_column: string;
      mapping_rule?: any;
    },
  ) {
    await this.uploadService.updateFieldMapping(
      uploadId,
      tenantId,
      body.field_name,
      body.source_column,
      body.mapping_rule,
    );

    return { success: true };
  }
}
```

### 4.2 UploadService erweitern

```typescript
// backend/src/modules/upload/upload.service.ts
// HINZUFÜGEN:

async getPreview(
  uploadId: string,
  tenantId: string,
  limit: number = 50,
): Promise<any[]> {
  const upload = await this.uploadRepo.findOne({
    where: { id: uploadId, tenant_id: tenantId },
  });

  if (!upload) {
    throw new NotFoundException('Upload not found');
  }

  // Load file and return first N rows
  const fileBuffer = await this.storageService.getFile(upload.storage_url);
  
  if (upload.mime_type.includes('csv')) {
    const parsed = Papa.parse(fileBuffer.toString('utf8'), {
      header: true,
      preview: limit,
    });
    return parsed.data;
  }

  // TODO: Excel, PDF preview
  return [];
}

async applyMappings(
  uploadId: string,
  tenantId: string,
  mappings: any[],
): Promise<void> {
  // Re-parse file with accepted mappings
  const upload = await this.uploadRepo.findOne({
    where: { id: uploadId, tenant_id: tenantId },
  });

  // Create temporary template from mappings
  const template = {
    mappings: mappings.reduce((acc, m) => {
      acc[m.field] = {
        keywords: [m.source_column],
        column: m.source_column,
      };
      return acc;
    }, {}),
  };

  // Parse with mappings
  await this.csvParser.parseWithTemplate(upload, template as any);

  // Update status
  await this.uploadRepo.update(uploadId, {
    status: 'parsed',
    reviewed_at: new Date(),
  });
}

async markForManualReview(
  uploadId: string,
  tenantId: string,
  reason: string,
): Promise<void> {
  await this.uploadRepo.update(
    { id: uploadId, tenant_id: tenantId },
    {
      status: 'needs_manual_review',
      parsing_issues: [{ type: 'rejected', reason, timestamp: new Date() }],
    },
  );
}

async updateFieldMapping(
  uploadId: string,
  tenantId: string,
  fieldName: string,
  sourceColumn: string,
  mappingRule?: any,
): Promise<void> {
  // Save to manual_mapping table
  await this.manualMappingRepo.save({
    upload_id: uploadId,
    field_name: fieldName,
    source_column: sourceColumn,
    mapping_rule: mappingRule,
    created_by: tenantId, // TODO: Get actual user ID
  });
}
```

---

## Phase 5: Project-Integration (Woche 5)

### 5.1 ProjectService vervollständigen

```typescript
// backend/src/modules/project/project.service.ts
// ERWEITERN mit fehlenden Methoden

async getProjectStats(
  projectId: string,
  tenantId: string,
): Promise<any> {
  const project = await this.projectRepo.findOne({
    where: { id: projectId, tenant_id: tenantId },
  });

  if (!project) {
    throw new NotFoundException('Project not found');
  }

  // Count uploads
  const uploadCount = await this.uploadRepo.count({
    where: { project_id: projectId, tenant_id: tenantId },
  });

  // Count shipments
  const shipmentCount = await this.shipmentRepo.count({
    where: { project_id: projectId, tenant_id: tenantId },
  });

  // Average completeness
  const completenessResult = await this.shipmentRepo
    .createQueryBuilder('s')
    .select('AVG(s.completeness_score)', 'avg_completeness')
    .where('s.project_id = :projectId', { projectId })
    .where('s.tenant_id = :tenantId', { tenantId })
    .getRawOne();

  // Count notes
  const noteCount = await this.consultantNoteRepo.count({
    where: { project_id: projectId },
  });

  return {
    project_id: projectId,
    upload_count: uploadCount,
    shipment_count: shipmentCount,
    avg_completeness: parseFloat(completenessResult.avg_completeness || '0'),
    note_count: noteCount,
    phase: project.phase,
    status: project.status,
  };
}

async addNote(
  projectId: string,
  tenantId: string,
  noteData: {
    note_type: string;
    content: string;
    priority?: string;
    related_to_upload_id?: string;
    related_to_shipment_id?: string;
  },
  userId: string,
): Promise<ConsultantNote> {
  return this.consultantNoteRepo.save({
    project_id: projectId,
    ...noteData,
    created_by: userId,
  });
}

async getNotes(
  projectId: string,
  tenantId: string,
  filters?: { status?: string; priority?: string },
): Promise<ConsultantNote[]> {
  const query = this.consultantNoteRepo
    .createQueryBuilder('n')
    .where('n.project_id = :projectId', { projectId })
    .orderBy('n.created_at', 'DESC');

  if (filters?.status) {
    query.andWhere('n.status = :status', { status: filters.status });
  }

  if (filters?.priority) {
    query.andWhere('n.priority = :priority', { priority: filters.priority });
  }

  return query.getMany();
}
```

### 5.2 ProjectController erweitern

```typescript
// backend/src/modules/project/project.controller.ts
// HINZUFÜGEN:

@Get(':id/stats')
async getStats(
  @Param('id') projectId: string,
  @TenantId() tenantId: string,
) {
  return this.projectService.getProjectStats(projectId, tenantId);
}

@Post(':id/notes')
async addNote(
  @Param('id') projectId: string,
  @TenantId() tenantId: string,
  @UserId() userId: string,
  @Body() noteData: CreateNoteDto,
) {
  return this.projectService.addNote(projectId, tenantId, noteData, userId);
}

@Get(':id/notes')
async getNotes(
  @Param('id') projectId: string,
  @TenantId() tenantId: string,
  @Query('status') status?: string,
  @Query('priority') priority?: string,
) {
  return this.projectService.getNotes(projectId, tenantId, { status, priority });
}
```

---

## Phase 6: Report-System finalisieren (Woche 6)

### 6.1 ReportService vervollständigen

```typescript
// backend/src/modules/report/report.service.ts
// VERVOLLSTÄNDIGEN:

async generateReport(
  projectId: string,
  tenantId: string,
  reportType: 'quick_check' | 'deep_dive' | 'final',
): Promise<Report> {
  // Get all shipments for project
  const shipments = await this.shipmentRepo.find({
    where: { project_id: projectId, tenant_id: tenantId },
    relations: ['benchmark'],
  });

  if (shipments.length === 0) {
    throw new Error('No shipments found for project');
  }

  // Calculate data completeness
  const completeness = shipments.reduce(
    (sum, s) => sum + (s.completeness_score || 0),
    0,
  ) / shipments.length;

  // Generate data snapshot
  const dataSnapshot = {
    summary: await this.aggregationService.calculateSummary(shipments),
    carriers: await this.aggregationService.aggregateByCarrier(projectId, tenantId),
    zones: await this.aggregationService.aggregateByZone(projectId, tenantId),
    quick_wins: await this.aggregationService.identifyQuickWins(projectId, tenantId),
  };

  // Get last report version
  const lastReport = await this.reportRepo.findOne({
    where: { project_id: projectId },
    order: { version: 'DESC' },
  });

  const version = (lastReport?.version || 0) + 1;

  // Save report
  return this.reportRepo.save({
    project_id: projectId,
    version,
    report_type: reportType,
    title: `${reportType} Report v${version}`,
    data_snapshot: dataSnapshot,
    data_completeness: completeness,
    shipment_count: shipments.length,
    generated_by: tenantId,
  });
}

async getReport(
  reportId: string,
  tenantId: string,
): Promise<Report> {
  const report = await this.reportRepo.findOne({
    where: { id: reportId },
    relations: ['project'],
  });

  if (!report || report.project.tenant_id !== tenantId) {
    throw new NotFoundException('Report not found');
  }

  return report;
}

async getProjectReports(
  projectId: string,
  tenantId: string,
): Promise<Report[]> {
  return this.reportRepo.find({
    where: { project_id: projectId },
    order: { version: 'DESC' },
  });
}
```

### 6.2 ReportAggregationService implementieren

```typescript
// backend/src/modules/report/report-aggregation.service.ts
// IMPLEMENTIEREN:

@Injectable()
export class ReportAggregationService {
  constructor(
    @InjectRepository(Shipment)
    private readonly shipmentRepo: Repository<Shipment>,
    @InjectRepository(ShipmentBenchmark)
    private readonly benchmarkRepo: Repository<ShipmentBenchmark>,
  ) {}

  async calculateSummary(shipments: Shipment[]): Promise<any> {
    const total = shipments.length;
    const withBenchmark = shipments.filter(s => s.benchmark).length;
    
    const totalActual = shipments.reduce((sum, s) => sum + (s.actual_total_amount || 0), 0);
    const totalExpected = shipments
      .filter(s => s.benchmark)
      .reduce((sum, s) => sum + (s.benchmark.expected_total_amount || 0), 0);
    
    const delta = totalActual - totalExpected;
    const savingsPotential = delta > 0 ? delta : 0;

    return {
      total_shipments: total,
      shipments_with_benchmark: withBenchmark,
      total_actual_amount: Math.round(totalActual * 100) / 100,
      total_expected_amount: Math.round(totalExpected * 100) / 100,
      total_delta: Math.round(delta * 100) / 100,
      savings_potential: Math.round(savingsPotential * 100) / 100,
      avg_delta_pct: withBenchmark > 0 
        ? Math.round((delta / totalExpected) * 10000) / 100 
        : 0,
    };
  }

  async aggregateByCarrier(
    projectId: string,
    tenantId: string,
  ): Promise<any[]> {
    const result = await this.shipmentRepo
      .createQueryBuilder('s')
      .leftJoin('s.benchmark', 'b')
      .select('s.carrier_name', 'carrier')
      .addSelect('COUNT(*)', 'shipment_count')
      .addSelect('SUM(s.actual_total_amount)', 'total_actual')
      .addSelect('SUM(b.expected_total_amount)', 'total_expected')
      .addSelect('SUM(s.actual_total_amount - b.expected_total_amount)', 'total_delta')
      .where('s.project_id = :projectId', { projectId })
      .andWhere('s.tenant_id = :tenantId', { tenantId })
      .groupBy('s.carrier_name')
      .orderBy('total_delta', 'DESC')
      .getRawMany();

    return result.map(r => ({
      carrier: r.carrier,
      shipment_count: parseInt(r.shipment_count),
      total_actual: Math.round(parseFloat(r.total_actual || '0') * 100) / 100,
      total_expected: Math.round(parseFloat(r.total_expected || '0') * 100) / 100,
      total_delta: Math.round(parseFloat(r.total_delta || '0') * 100) / 100,
    }));
  }

  async aggregateByZone(
    projectId: string,
    tenantId: string,
  ): Promise<any[]> {
    const result = await this.shipmentRepo
      .createQueryBuilder('s')
      .leftJoin('s.benchmark', 'b')
      .select('s.zone', 'zone')
      .addSelect('COUNT(*)', 'shipment_count')
      .addSelect('SUM(s.actual_total_amount)', 'total_actual')
      .addSelect('SUM(b.expected_total_amount)', 'total_expected')
      .where('s.project_id = :projectId', { projectId })
      .andWhere('s.tenant_id = :tenantId', { tenantId })
      .groupBy('s.zone')
      .orderBy('s.zone', 'ASC')
      .getRawMany();

    return result.map(r => ({
      zone: r.zone,
      shipment_count: parseInt(r.shipment_count),
      total_actual: Math.round(parseFloat(r.total_actual || '0') * 100) / 100,
      total_expected: Math.round(parseFloat(r.total_expected || '0') * 100) / 100,
    }));
  }

  async identifyQuickWins(
    projectId: string,
    tenantId: string,
  ): Promise<any[]> {
    // Find shipments with highest overpay (top 20)
    const result = await this.shipmentRepo
      .createQueryBuilder('s')
      .leftJoin('s.benchmark', 'b')
      .select([
        's.id',
        's.date',
        's.carrier_name',
        's.origin_zip',
        's.dest_zip',
        's.actual_total_amount',
        'b.expected_total_amount',
        'b.classification',
      ])
      .where('s.project_id = :projectId', { projectId })
      .andWhere('s.tenant_id = :tenantId', { tenantId })
      .andWhere('b.classification = :classification', { classification: 'drüber' })
      .orderBy('s.actual_total_amount - b.expected_total_amount', 'DESC')
      .limit(20)
      .getMany();

    return result.map(s => ({
      shipment_id: s.id,
      date: s.date,
      carrier: s.carrier_name,
      route: `${s.origin_zip} → ${s.dest_zip}`,
      actual: s.actual_total_amount,
      expected: s.benchmark?.expected_total_amount,
      delta: s.actual_total_amount - (s.benchmark?.expected_total_amount || 0),
      delta_pct: s.benchmark
        ? Math.round(
            ((s.actual_total_amount - s.benchmark.expected_total_amount) /
              s.benchmark.expected_total_amount) *
              10000,
          ) / 100
        : 0,
    }));
  }
}
```

---

## Phase 7: Frontend (Wochen 7-10)

### 7.1 Project Overview (Woche 7)

```typescript
// frontend/src/pages/Projects.tsx
// NEU ERSTELLEN

import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

export const ProjectsPage: React.FC = () => {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadProjects();
  }, []);

  const loadProjects = async () => {
    const response = await api.get('/projects');
    setProjects(response.data);
    setLoading(false);
  };

  if (loading) return <div>Loading...</div>;

  return (
    <div className="container mx-auto p-6">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Projects</h1>
        <Link
          to="/projects/new"
          className="bg-blue-600 text-white px-4 py-2 rounded"
        >
          New Project
        </Link>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {projects.map((project: any) => (
          <Link
            key={project.id}
            to={`/projects/${project.id}`}
            className="border rounded-lg p-6 hover:shadow-lg transition"
          >
            <h3 className="text-xl font-semibold mb-2">{project.name}</h3>
            <p className="text-gray-600 mb-4">{project.customer_name}</p>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Phase: {project.phase}</span>
              <span className="text-gray-500">Status: {project.status}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
};
```

### 7.2 Upload Review UI (Woche 8)

```typescript
// frontend/src/pages/UploadReview.tsx
// NEU ERSTELLEN

import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';

export const UploadReviewPage: React.FC = () => {
  const { uploadId } = useParams();
  const [reviewData, setReviewData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadReviewData();
  }, [uploadId]);

  const loadReviewData = async () => {
    const response = await api.get(`/uploads/${uploadId}/review`);
    setReviewData(response.data);
    setLoading(false);
  };

  const handleAccept = async () => {
    await api.post(`/uploads/${uploadId}/review/accept`, {
      mappings: reviewData.suggested_mappings,
      save_as_template: false,
    });
    // Navigate to next step
  };

  const handleReject = async () => {
    await api.post(`/uploads/${uploadId}/review/reject`, {
      reason: 'Mappings incorrect',
    });
    // Navigate back
  };

  if (loading) return <div>Loading...</div>;

  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">Review Upload</h1>

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4">LLM Analysis</h2>
        <div className="mb-4">
          <span className="font-medium">File Type:</span>{' '}
          {reviewData.llm_analysis.file_type}
        </div>
        <div className="mb-4">
          <span className="font-medium">Confidence:</span>{' '}
          {(reviewData.llm_analysis.confidence * 100).toFixed(0)}%
        </div>
        <div className="mb-4">
          <span className="font-medium">Description:</span>{' '}
          {reviewData.llm_analysis.description}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4">Suggested Mappings</h2>
        <table className="w-full">
          <thead>
            <tr className="border-b">
              <th className="text-left p-2">Field</th>
              <th className="text-left p-2">Source Column</th>
              <th className="text-left p-2">Confidence</th>
              <th className="text-left p-2">Sample Values</th>
            </tr>
          </thead>
          <tbody>
            {reviewData.suggested_mappings.map((mapping: any, i: number) => (
              <tr key={i} className="border-b">
                <td className="p-2">{mapping.field}</td>
                <td className="p-2">{mapping.column}</td>
                <td className="p-2">
                  {(mapping.confidence * 100).toFixed(0)}%
                </td>
                <td className="p-2">
                  {mapping.sample_values.slice(0, 3).join(', ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4">Preview (First 10 rows)</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                {Object.keys(reviewData.preview[0] || {}).map((key) => (
                  <th key={key} className="text-left p-2">
                    {key}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {reviewData.preview.slice(0, 10).map((row: any, i: number) => (
                <tr key={i} className="border-b">
                  {Object.values(row).map((val: any, j: number) => (
                    <td key={j} className="p-2">
                      {String(val)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex gap-4">
        <button
          onClick={handleAccept}
          className="bg-green-600 text-white px-6 py-2 rounded"
        >
          Accept & Parse
        </button>
        <button
          onClick={handleReject}
          className="bg-red-600 text-white px-6 py-2 rounded"
        >
          Reject
        </button>
      </div>
    </div>
  );
};
```

### 7.3 Report Viewer (Woche 9-10)

```typescript
// frontend/src/pages/ReportViewer.tsx
// NEU ERSTELLEN

import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';

export const ReportViewerPage: React.FC = () => {
  const { reportId } = useParams();
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadReport();
  }, [reportId]);

  const loadReport = async () => {
    const response = await api.get(`/reports/${reportId}`);
    setReport(response.data);
    setLoading(false);
  };

  if (loading) return <div>Loading...</div>;

  const { data_snapshot } = report;

  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">
        {report.title}
      </h1>

      {/* Summary Section */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4">Summary</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <div className="text-gray-600 text-sm">Total Shipments</div>
            <div className="text-2xl font-bold">
              {data_snapshot.summary.total_shipments}
            </div>
          </div>
          <div>
            <div className="text-gray-600 text-sm">Total Actual</div>
            <div className="text-2xl font-bold">
              €{data_snapshot.summary.total_actual_amount.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-gray-600 text-sm">Total Expected</div>
            <div className="text-2xl font-bold">
              €{data_snapshot.summary.total_expected_amount.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-gray-600 text-sm">Savings Potential</div>
            <div className="text-2xl font-bold text-green-600">
              €{data_snapshot.summary.savings_potential.toLocaleString()}
            </div>
          </div>
        </div>
      </div>

      {/* Carrier Breakdown */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4">By Carrier</h2>
        <table className="w-full">
          <thead>
            <tr className="border-b">
              <th className="text-left p-2">Carrier</th>
              <th className="text-right p-2">Shipments</th>
              <th className="text-right p-2">Actual</th>
              <th className="text-right p-2">Expected</th>
              <th className="text-right p-2">Delta</th>
            </tr>
          </thead>
          <tbody>
            {data_snapshot.carriers.map((c: any, i: number) => (
              <tr key={i} className="border-b">
                <td className="p-2">{c.carrier}</td>
                <td className="text-right p-2">{c.shipment_count}</td>
                <td className="text-right p-2">
                  €{c.total_actual.toLocaleString()}
                </td>
                <td className="text-right p-2">
                  €{c.total_expected.toLocaleString()}
                </td>
                <td
                  className={`text-right p-2 ${
                    c.total_delta > 0 ? 'text-red-600' : 'text-green-600'
                  }`}
                >
                  €{c.total_delta.toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Quick Wins */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">Top Overpayments</h2>
        <table className="w-full">
          <thead>
            <tr className="border-b">
              <th className="text-left p-2">Date</th>
              <th className="text-left p-2">Carrier</th>
              <th className="text-left p-2">Route</th>
              <th className="text-right p-2">Actual</th>
              <th className="text-right p-2">Expected</th>
              <th className="text-right p-2">Delta</th>
              <th className="text-right p-2">%</th>
            </tr>
          </thead>
          <tbody>
            {data_snapshot.quick_wins.map((qw: any, i: number) => (
              <tr key={i} className="border-b">
                <td className="p-2">{qw.date}</td>
                <td className="p-2">{qw.carrier}</td>
                <td className="p-2">{qw.route}</td>
                <td className="text-right p-2">€{qw.actual.toFixed(2)}</td>
                <td className="text-right p-2">€{qw.expected.toFixed(2)}</td>
                <td className="text-right p-2 text-red-600">
                  €{qw.delta.toFixed(2)}
                </td>
                <td className="text-right p-2 text-red-600">
                  +{qw.delta_pct}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
```

---

## Phase 8: Testing & Integration (Wochen 11-12)

### 8.1 Integration Tests

```typescript
// backend/test/integration/project-workflow.spec.ts
// NEU ERSTELLEN

import { Test } from '@nestjs/testing';
import { INestApplication } from '@nestjs/common';
import { AppModule } from '../../src/app.module';
import * as request from 'supertest';

describe('Project Workflow Integration', () => {
  let app: INestApplication;
  let authToken: string;
  let projectId: string;
  let uploadId: string;

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();

    app = moduleRef.createNestApplication();
    await app.init();

    // Login
    const loginResponse = await request(app.getHttpServer())
      .post('/auth/login')
      .send({ email: 'test@freightwatch.com', password: 'password123' });
    
    authToken = loginResponse.body.access_token;
  });

  it('should create a project', async () => {
    const response = await request(app.getHttpServer())
      .post('/projects')
      .set('Authorization', `Bearer ${authToken}`)
      .send({
        name: 'Test Project',
        customer_name: 'Test Customer',
        phase: 'quick_check',
      })
      .expect(201);

    projectId = response.body.id;
    expect(projectId).toBeDefined();
  });

  it('should upload file to project', async () => {
    const response = await request(app.getHttpServer())
      .post('/uploads')
      .set('Authorization', `Bearer ${authToken}`)
      .field('project_id', projectId)
      .attach('file', 'test/fixtures/test-shipments.csv')
      .expect(201);

    uploadId = response.body.id;
    expect(uploadId).toBeDefined();
  });

  it('should process upload with template or LLM', async () => {
    // Wait for processing
    await new Promise(resolve => setTimeout(resolve, 5000));

    const response = await request(app.getHttpServer())
      .get(`/uploads/${uploadId}`)
      .set('Authorization', `Bearer ${authToken}`)
      .expect(200);

    expect(['parsed', 'needs_review']).toContain(response.body.status);
  });

  it('should get review data if needs review', async () => {
    const uploadResponse = await request(app.getHttpServer())
      .get(`/uploads/${uploadId}`)
      .set('Authorization', `Bearer ${authToken}`);

    if (uploadResponse.body.status === 'needs_review') {
      const reviewResponse = await request(app.getHttpServer())
        .get(`/uploads/${uploadId}/review`)
        .set('Authorization', `Bearer ${authToken}`)
        .expect(200);

      expect(reviewResponse.body.llm_analysis).toBeDefined();
      expect(reviewResponse.body.suggested_mappings).toBeDefined();
    }
  });

  it('should generate report', async () => {
    const response = await request(app.getHttpServer())
      .post('/reports/generate')
      .set('Authorization', `Bearer ${authToken}`)
      .send({
        project_id: projectId,
        report_type: 'quick_check',
      })
      .expect(201);

    expect(response.body.id).toBeDefined();
    expect(response.body.data_snapshot).toBeDefined();
  });

  afterAll(async () => {
    await app.close();
  });
});
```

### 8.2 E2E Test mit echten Daten

```bash
# backend/test/fixtures/prepare-test-data.sh
# ERSTELLEN

#!/bin/bash

# Prepare test environment with real MECU data
psql -d freightwatch_test << EOF
-- Truncate all tables
TRUNCATE TABLE shipment CASCADE;
TRUNCATE TABLE upload CASCADE;
TRUNCATE TABLE project CASCADE;

-- Insert test project
INSERT INTO project (id, tenant_id, name, phase, status)
VALUES (
  'test-project-001',
  'c7b3d8e6-1234-4567-8901-123456789012',
  'MECU Test Project',
  'quick_check',
  'in_progress'
);
EOF

# Upload MECU CSV
curl -X POST http://localhost:3000/uploads \
  -H "Authorization: Bearer $TEST_TOKEN" \
  -F "project_id=test-project-001" \
  -F "file=@test/fixtures/mecu/sample.csv"

echo "Test data prepared"
```

---

## Implementation Checklist

### Week 1: Database
- [ ] Execute migration 003_refactor_to_project_workflow.sql
- [ ] Verify old tables dropped (service_alias, service_catalog, tariff_rule, surcharge_catalog)
- [ ] Verify new tables created (project, consultant_note, parsing_template, manual_mapping, report)
- [ ] Generate migration for entity updates
- [ ] Run entity update migration
- [ ] Test RLS with new tables

### Week 2: Service Refactoring
- [ ] Simplify ServiceMapperService (remove DB lookups)
- [ ] Update ServiceMapperService tests
- [ ] Clean up ParsingModule imports
- [ ] Refactor TariffEngineService (use carrier.conversion_rules)
- [ ] Test service-level normalization

### Week 3: Upload Processor
- [ ] Rewrite UploadProcessor with hybrid logic
- [ ] Implement Template → LLM fallback
- [ ] Extend CsvParserService with template parsing
- [ ] Add completeness calculation
- [ ] Test template matching flow

### Week 4: Review Workflow
- [ ] Create UploadReviewController
- [ ] Implement getReviewData endpoint
- [ ] Implement acceptMappings endpoint
- [ ] Implement rejectMappings endpoint
- [ ] Extend UploadService with preview/apply methods
- [ ] Test review workflow end-to-end

### Week 5: Project Integration
- [ ] Extend ProjectService with stats/notes methods
- [ ] Extend ProjectController with new endpoints
- [ ] Test project stats calculation
- [ ] Test consultant notes CRUD

### Week 6: Report System
- [ ] Complete ReportService.generateReport()
- [ ] Implement ReportAggregationService methods
- [ ] Test report generation with MECU data
- [ ] Test carrier/zone aggregations
- [ ] Test quick wins identification

### Week 7: Frontend - Projects
- [ ] Create ProjectsPage
- [ ] Create ProjectDetailPage
- [ ] Implement project creation form
- [ ] Test project navigation

### Week 8: Frontend - Upload Review
- [ ] Create UploadReviewPage
- [ ] Implement mapping editor
- [ ] Implement accept/reject actions
- [ ] Test review workflow in UI

### Week 9-10: Frontend - Reports
- [ ] Create ReportViewerPage
- [ ] Implement summary cards
- [ ] Implement carrier/zone tables
- [ ] Implement quick wins table
- [ ] Test report visualization

### Week 11-12: Testing & Deployment
- [ ] Write integration tests
- [ ] E2E test with MECU data
- [ ] Performance testing (10k shipments)
- [ ] Deploy to staging
- [ ] Internal testing
- [ ] Production deployment

---

## Success Metrics

**After Week 4:**
- ✅ LLM can analyze unknown files
- ✅ Template matching works for known formats
- ✅ Review workflow functional

**After Week 6:**
- ✅ Reports generated with aggregations
- ✅ Backend API complete
- ✅ All services integrated

**After Week 10:**
- ✅ Frontend functional
- ✅ Full workflow: Project → Upload → Review → Report
- ✅ Ready for internal testing

**After Week 12:**
- ✅ Production deployment
- ✅ System stable and tested
- ✅ First consultants can use the tool

---

## Critical Dependencies

1. **Anthropic API Key** - Required for LLM analysis
2. **Storage Solution** - S3 or local file storage configured
3. **PostgreSQL 14+** - For RLS and JSONB
4. **Redis** - For Bull queue
5. **Node.js 18+** - Runtime

---

## Risk Mitigation

### Risk: LLM costs too high
**Mitigation:** 
- Cost tracking in every LLM call
- Monthly budget alerts
- Template learning to reduce LLM usage over time

### Risk: Template matching accuracy low
**Mitigation:**
- Start with 3-5 high-quality templates
- Allow consultants to correct and save templates
- Monitor template usage and accuracy

### Risk: Frontend development delays
**Mitigation:**
- API-first approach - backend works standalone
- Simple UI first, polish later
- Use Tailwind for fast styling

### Risk: Migration breaks existing data
**Mitigation:**
- Full database backup before migration
- Test migration on staging first
- Rollback plan documented

---

**Next Steps:** Start with Week 1 - Database consistency. Once migration is confirmed working, proceed to service refactoring.