**FreightWatch MVP Refactoring Guide v3**

**Berater-Tool mit LLM-Assisted Parsing**

**Erstellt**: 2025-01-07  
**Zielgruppe**: Entwickler (Claude Code)  
**Kontext**: Beratungs-Tool, nicht Self-Service SaaS

**Executive Summary**

**Kernprozess**: Berater analysiert Kundendaten in iterativen Phasen:

- **Quick-Check** (70% Daten) → Erste Potenzial-Schätzung
- **Deep-Dive** (100% Daten) → Präzise Analyse
- **Client Portal** (später) → Kunde bekommt Self-Service-Zugang

**Haupt-Challenge**: Kunden liefern chaotische, heterogene Daten

- Excel ohne Header, kryptische Spaltennamen
- PDFs in verschiedenen Layouts
- Mischung aus Sendungslisten, Rechnungen, Tarifen, Routendokus

**Lösung**: Hybrid Template + LLM Parsing

- **Template-Parser**: Bekannte Formate (schnell, kostenlos, deterministisch)
- **LLM-Parser**: Unbekannte Formate (flexibel, teuer, braucht Review)
- **Template-Learning**: LLM-Outputs werden zu Templates → System lernt

**Phase 1: Foundation - Project & Upload Management (Wochen 1-6)**

**1.1 Neue Datenbank-Strukturen**

**Problem**: Aktuelles System kennt nur upload → direkt zu shipment  
**Lösung**: Project-Workspace mit Kuratierungs-Flow

sql

_\-- ============================================_

_\-- PROJECT / WORKSPACE_

_\-- ============================================_

CREATE TABLE project (

id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

tenant_id UUID NOT NULL REFERENCES tenant(id),

name VARCHAR(255) NOT NULL,

customer_name VARCHAR(255), _\-- "MECU Metallhalbzeug"_

phase VARCHAR(50) DEFAULT 'quick_check',

_\-- 'quick_check' | 'deep_dive' | 'ongoing' | 'archived'_

status VARCHAR(50) DEFAULT 'draft',

_\-- 'draft' | 'analysis' | 'report_ready' | 'closed'_

consultant_id UUID, _\-- Welcher Berater führt das Projekt_

metadata JSONB, _\-- Projekt-spezifische Infos_

_\-- e.g. {"industry": "steel", "annual_freight_volume_eur": 500000}_

created_at TIMESTAMPTZ DEFAULT now(),

updated_at TIMESTAMPTZ DEFAULT now()

);

ALTER TABLE project ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON project

USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX idx_project_tenant ON project(tenant_id);

CREATE INDEX idx_project_consultant ON project(consultant_id);

_\-- ============================================_

_\-- UPLOAD erweitern für LLM-Workflow_

_\-- ============================================_

ALTER TABLE upload ADD COLUMN project_id UUID REFERENCES project(id);

ALTER TABLE upload ADD COLUMN parse_method VARCHAR(50);

_\-- 'template' | 'llm' | 'manual' | 'heuristic' | 'hybrid'_

ALTER TABLE upload ADD COLUMN confidence DECIMAL(3,2);

_\-- 0.0-1.0: Wie sicher ist das Parsing?_

ALTER TABLE upload ADD COLUMN suggested_mappings JSONB;

_\-- LLM's vorgeschlagene Feld-Mappings_

ALTER TABLE upload ADD COLUMN llm_analysis JSONB;

_\-- Vollständiger LLM-Output für Audit_

ALTER TABLE upload ADD COLUMN reviewed_by UUID;

ALTER TABLE upload ADD COLUMN reviewed_at TIMESTAMPTZ;

_\-- Berater-Review Tracking_

ALTER TABLE upload ADD COLUMN parsing_issues JSONB\[\];

_\-- Liste von Problemen die beim Parsing auftraten_

CREATE INDEX idx_upload_project ON upload(project_id);

CREATE INDEX idx_upload_status ON upload(status);

_\-- ============================================_

_\-- MANUAL MAPPING (Berater-Kuration)_

_\-- ============================================_

CREATE TABLE manual_mapping (

id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

upload_id UUID NOT NULL REFERENCES upload(id),

field_name VARCHAR(100) NOT NULL, _\-- 'origin_zip', 'weight_kg', etc._

source_column VARCHAR(100), _\-- 'Column G' oder 'Von PLZ'_

mapping_rule JSONB, _\-- Transformation-Regel_

_\-- e.g. {"type": "direct"} oder {"type": "regex", "pattern": "\\\\d+", "strip": " kg"}_

confidence DECIMAL(3,2), _\-- Berater's Confidence_

notes TEXT, _\-- Berater-Notizen_

created_by UUID NOT NULL, _\-- Welcher Berater_

created_at TIMESTAMPTZ DEFAULT now()

);

ALTER TABLE manual_mapping ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON manual_mapping

USING (upload_id IN (SELECT id FROM upload WHERE tenant_id = current_setting('app.current_tenant')::UUID));

_\-- ============================================_

_\-- PARSING TEMPLATE (Template-Learning)_

_\-- ============================================_

CREATE TABLE parsing_template (

id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

tenant_id UUID REFERENCES tenant(id), _\-- NULL = global template_

name VARCHAR(255) NOT NULL,

description TEXT,

file_type VARCHAR(50) NOT NULL, _\-- 'csv' | 'excel' | 'pdf'_

detection JSONB NOT NULL,

_\-- Wie erkennen wir dieses Format?_

_\-- {_

_\-- "file_name_pattern": ".\*sendungen.\*\\\\.xlsx",_

_\-- "header_pattern": "Datum.\*Von.\*Nach",_

_\-- "sample_hash": "abc123..."_

_\-- }_

mappings JSONB NOT NULL,

_\-- Feld-Mappings_

_\-- \[_

_\-- {"field": "origin_zip", "column": "C", "pattern": "..."},_

_\-- {"field": "weight_kg", "column": "K", "transformation": "strip ' kg'"}_

_\-- \]_

source VARCHAR(50) DEFAULT 'manual',

_\-- 'manual' | 'llm_assisted' | 'learned'_

verified_by UUID,

verified_at TIMESTAMPTZ,

usage_count INTEGER DEFAULT 0,

last_used_at TIMESTAMPTZ,

created_at TIMESTAMPTZ DEFAULT now()

);

ALTER TABLE parsing_template ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON parsing_template

USING (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX idx_template_file_type ON parsing_template(file_type);

CREATE INDEX idx_template_tenant ON parsing_template(tenant_id);

_\-- ============================================_

_\-- CONSULTANT NOTES_

_\-- ============================================_

CREATE TABLE consultant_note (

id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

project_id UUID NOT NULL REFERENCES project(id),

note_type VARCHAR(50) NOT NULL,

_\-- 'finding' | 'question' | 'action_item' | 'observation'_

content TEXT NOT NULL,

related_to_upload_id UUID REFERENCES upload(id),

related_to_shipment_id UUID REFERENCES shipment(id),

priority VARCHAR(20), _\-- 'low' | 'medium' | 'high'_

status VARCHAR(50) DEFAULT 'open', _\-- 'open' | 'resolved' | 'deferred'_

created_by UUID NOT NULL,

created_at TIMESTAMPTZ DEFAULT now(),

resolved_at TIMESTAMPTZ

);

ALTER TABLE consultant_note ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON consultant_note

USING (project_id IN (SELECT id FROM project WHERE tenant_id = current_setting('app.current_tenant')::UUID));

CREATE INDEX idx_note_project ON consultant_note(project_id);

CREATE INDEX idx_note_status ON consultant_note(status);

_\-- ============================================_

_\-- SHIPMENT erweitern für Partial Data_

_\-- ============================================_

ALTER TABLE shipment ADD COLUMN completeness_score DECIMAL(3,2);

_\-- 0.0-1.0: Wie vollständig sind die Daten?_

ALTER TABLE shipment ADD COLUMN missing_fields TEXT\[\];

_\-- \['actual_cost', 'toll_amount'\] - Was fehlt noch?_

ALTER TABLE shipment ADD COLUMN data_quality_issues JSONB;

_\-- Spezifische Quality-Issues_

_\-- \[{"field": "weight_kg", "issue": "below_minimum", "value": 5}\]_

ALTER TABLE shipment ADD COLUMN consultant_notes TEXT;

_\-- Schnelle Notizen vom Berater_

ALTER TABLE shipment ADD COLUMN manual_override BOOLEAN DEFAULT FALSE;

_\-- Wurde manuell korrigiert?_

**1.2 Project-Management Module**

typescript

_// src/modules/project/project.module.ts_

import { Module } from '@nestjs/common';

import { TypeOrmModule } from '@nestjs/typeorm';

import { Project } from './entities/project.entity';

import { ConsultantNote } from './entities/consultant-note.entity';

import { ProjectService } from './project.service';

import { ProjectController } from './project.controller';

@Module({

imports: \[TypeOrmModule.forFeature(\[Project, ConsultantNote\])\],

providers: \[ProjectService\],

controllers: \[ProjectController\],

exports: \[ProjectService\],

})

export class ProjectModule {}

typescript

_// src/modules/project/entities/project.entity.ts_

import {

Entity,

Column,

PrimaryGeneratedColumn,

CreateDateColumn,

UpdateDateColumn,

OneToMany,

} from 'typeorm';

import { Upload } from '../../upload/entities/upload.entity';

@Entity('project')

export class Project {

@PrimaryGeneratedColumn('uuid')

id: string;

@Column('uuid')

tenant_id: string;

@Column()

name: string;

@Column({ nullable: true })

customer_name: string;

@Column({ default: 'quick_check' })

phase: 'quick_check' | 'deep_dive' | 'ongoing' | 'archived';

@Column({ default: 'draft' })

status: 'draft' | 'analysis' | 'report_ready' | 'closed';

@Column('uuid', { nullable: true })

consultant_id: string;

@Column('jsonb', { nullable: true })

metadata: any;

@CreateDateColumn()

created_at: Date;

@UpdateDateColumn()

updated_at: Date;

@OneToMany(() => Upload, upload => upload.project)

uploads: Upload\[\];

}

typescript

_// src/modules/project/project.controller.ts_

import { Controller, Post, Get, Param, Body, Patch } from '@nestjs/common';

import { TenantId } from '../auth/tenant.decorator';

import { ProjectService } from './project.service';

@Controller('api/projects')

export class ProjectController {

constructor(private readonly projectService: ProjectService) {}

@Post()

async createProject(

@TenantId() tenantId: string,

@Body() createDto: CreateProjectDto

) {

const project = await this.projectService.create(tenantId, createDto);

return { success: true, project };

}

@Get()

async listProjects(@TenantId() tenantId: string) {

const projects = await this.projectService.findAll(tenantId);

return { success: true, projects };

}

@Get(':id')

async getProject(

@Param('id') projectId: string,

@TenantId() tenantId: string

) {

const project = await this.projectService.findOne(projectId, tenantId);

return { success: true, project };

}

@Patch(':id')

async updateProject(

@Param('id') projectId: string,

@TenantId() tenantId: string,

@Body() updateDto: UpdateProjectDto

) {

const project = await this.projectService.update(projectId, tenantId, updateDto);

return { success: true, project };

}

}

**Phase 2: LLM-Integration (Wochen 7-14)**

**2.1 LLM-Parser Service**

typescript

_// src/modules/parsing/llm-parser.service.ts_

import { Injectable, Logger } from '@nestjs/common';

import Anthropic from '@anthropic-ai/sdk';

import \* as fs from 'fs/promises';

export interface LlmParseResult {

file_type: 'shipment_list' | 'invoice' | 'tariff_table' | 'route_documentation' | 'unknown';

confidence: number;

description: string;

column_mappings?: ColumnMapping\[\];

tariff_structure?: TariffStructure;

issues: string\[\];

suggested_actions: string\[\];

needs_review: boolean;

}

export interface ColumnMapping {

column: string;

field: string;

confidence: number;

pattern?: string;

sample_values: string\[\];

}

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

): Promise&lt;LlmParseResult&gt; {

this.logger.log(\`LLM analyzing: \${fileName} (\${mimeType})\`);

_// 1. Extract text/structure from file_

const content = await this.extractContent(fileBuffer, mimeType);

_// 2. Build analysis prompt_

const prompt = this.buildAnalysisPrompt(content, fileName, mimeType);

_// 3. Call Claude_

const response = await this.anthropic.messages.create({

model: 'claude-sonnet-4-20250514',

max_tokens: 4000,

temperature: 0, _// Deterministic for consistency_

messages: \[{

role: 'user',

content: prompt

}\]

});

_// 4. Parse structured output_

const textContent = response.content.find(c => c.type === 'text');

if (!textContent || textContent.type !== 'text') {

throw new Error('No text response from LLM');

}

let analysis: any;

try {

_// Extract JSON from response (handle markdown code blocks)_

const jsonMatch = textContent.text.match(/\`\`\`json\\n(\[\\s\\S\]\*?)\\n\`\`\`/);

const jsonText = jsonMatch ? jsonMatch\[1\] : textContent.text;

analysis = JSON.parse(jsonText);

} catch (error) {

this.logger.error('Failed to parse LLM response:', textContent.text);

throw new Error('LLM returned invalid JSON');

}

const result: LlmParseResult = {

file_type: analysis.file_type || 'unknown',

confidence: analysis.confidence || 0,

description: analysis.description || '',

column_mappings: analysis.column_mappings || \[\],

tariff_structure: analysis.tariff_structure,

issues: analysis.issues || \[\],

suggested_actions: analysis.suggested_actions || \[\],

needs_review: analysis.confidence &lt; 0.85 || analysis.issues.length &gt; 0

};

this.logger.log(\`LLM analysis complete: \${result.file_type} (confidence: \${result.confidence})\`);

return result;

}

private async extractContent(buffer: Buffer, mimeType: string): Promise&lt;string&gt; {

_// For CSV/text files_

if (mimeType.includes('text') || mimeType.includes('csv')) {

return buffer.toString('utf-8');

}

_// For Excel files_

if (mimeType.includes('spreadsheet') || mimeType.includes('excel')) {

_// Use existing Excel parser to extract text representation_

_// For now, simple conversion (you'd use xlsx library)_

return buffer.toString('utf-8').substring(0, 5000);

}

_// For PDFs_

if (mimeType.includes('pdf')) {

_// Use pdf-parse to extract text_

const pdfParse = require('pdf-parse');

const data = await pdfParse(buffer);

return data.text;

}

return buffer.toString('utf-8').substring(0, 5000);

}

private buildAnalysisPrompt(content: string, fileName: string, mimeType: string): string {

return \`You are analyzing a freight/logistics data file for a cost analysis system used by consultants.

\*\*File Information:\*\*

\- File name: \${fileName}

\- MIME type: \${mimeType}

\*\*Content preview (first 2000 chars):\*\*

\${content.substring(0, 2000)}

\*\*Your Task:\*\*

Analyze this file and determine what type of logistics data it contains, then extract the structure.

\*\*Respond with JSON (no markdown, just JSON):\*\*

{

"file_type": "shipment_list" | "invoice" | "tariff_table" | "route_documentation" | "unknown",

"confidence": 0.0-1.0,

"description": "1-2 sentence description of what this file contains",

// IF file_type is "shipment_list" or "invoice":

"column_mappings": \[

{

"column": "Column letter/number or header name (e.g., 'C', 'Column 5', 'Von PLZ')",

"field": "One of: origin_zip | dest_zip | origin_city | dest_city | weight_kg | length_m | volume_cbm | pallets | carrier_name | date | service_level | reference_number | actual_cost | diesel_pct | toll_amount | other",

"confidence": 0.0-1.0,

"pattern": "Any transformation needed (e.g., 'strip kg suffix', 'convert comma to dot')",

"sample_values": \["up to 3 example values from this column"\]

}

\],

// IF file_type is "tariff_table":

"tariff_structure": {

"carrier_name": "Detected carrier name if visible",

"zone_system": "Brief description of how zones are structured",

"rate_structure": "Brief description of the rate table (e.g., 'weight brackets 50-3000kg, 8 zones')",

"suggested_template": "Description of patterns that could be used for automated extraction"

},

"issues": \[

"List any data quality issues: mixed formats, missing data, unclear structure, etc."

\],

"suggested_actions": \[

"Actions the consultant should take (e.g., 'Ask customer about currency in column P', 'Clarify if column F is pickup or delivery date')"

\]

}

\*\*Important Guidelines:\*\*

\- Only suggest field mappings you're confident about (confidence >= 0.7)

\- For ambiguous columns, note them in "issues" rather than guessing

\- Be conservative - false negatives are better than false positives

\- Focus on common freight logistics fields listed above

\- If you see carrier-specific codes or references, mention them in description\`;

}

}

**2.2 Hybrid Upload-Processor**

typescript

_// src/modules/upload/upload-processor.service.ts (erweitert)_

import { Processor, Process } from '@nestjs/bull';

import { Injectable, Logger } from '@nestjs/common';

import { Job } from 'bull';

import { LlmParserService } from '../parsing/llm-parser.service';

import { TemplateMatcherService } from '../parsing/template-matcher.service';

import { HeuristicParserService } from '../parsing/heuristic-parser.service';

@Processor('upload')

@Injectable()

export class UploadProcessor {

private readonly logger = new Logger(UploadProcessor.name);

constructor(

private readonly templateMatcher: TemplateMatcherService,

private readonly heuristicParser: HeuristicParserService,

private readonly llmParser: LlmParserService,

private readonly uploadRepo: Repository&lt;Upload&gt;,

) {}

@Process('parse-file')

async handleParseFile(job: Job): Promise&lt;void&gt; {

const { uploadId, tenantId } = job.data;

this.logger.log(\`Processing upload \${uploadId} for tenant \${tenantId}\`);

const upload = await this.uploadRepo.findOne({

where: { id: uploadId, tenant_id: tenantId }

});

if (!upload) {

throw new Error(\`Upload \${uploadId} not found\`);

}

try {

_// STEP 1: Try template-based parsing_

const templateMatch = await this.templateMatcher.findMatch(upload);

if (templateMatch && templateMatch.confidence > 0.9) {

this.logger.log(\`High-confidence template match: \${templateMatch.template.name}\`);

await this.parseWithTemplate(upload, templateMatch.template);

await this.uploadRepo.update(uploadId, {

status: 'parsed',

parse_method: 'template',

confidence: templateMatch.confidence,

parsed_at: new Date()

});

return;

}

_// STEP 2: Try heuristic parsing (simple rules for standard formats)_

if (upload.mime_type.includes('csv') || upload.mime_type.includes('text')) {

const heuristicResult = await this.heuristicParser.tryParse(upload);

if (heuristicResult && heuristicResult.confidence > 0.8) {

this.logger.log('Heuristic parser successful');

await this.parseWithHeuristics(upload, heuristicResult);

await this.uploadRepo.update(uploadId, {

status: 'parsed',

parse_method: 'heuristic',

confidence: heuristicResult.confidence,

parsed_at: new Date()

});

return;

}

}

_// STEP 3: Unknown format → LLM analysis_

this.logger.log(\`Unknown format for \${upload.file_name}, using LLM parser\`);

const fileBuffer = await this.loadFileBuffer(upload.storage_url);

const llmResult = await this.llmParser.analyzeUnknownFile(

fileBuffer,

upload.file_name,

upload.mime_type

);

_// Save LLM analysis_

await this.uploadRepo.update(uploadId, {

status: 'needs_review',

parse_method: 'llm',

confidence: llmResult.confidence,

suggested_mappings: llmResult.column_mappings,

llm_analysis: llmResult,

parsing_issues: llmResult.issues

});

this.logger.log(\`LLM analysis complete, confidence: \${llmResult.confidence}, needs review: \${llmResult.needs_review}\`);

} catch (error) {

this.logger.error(\`Error processing upload \${uploadId}:\`, error);

await this.uploadRepo.update(uploadId, {

status: 'error',

parse_errors: { message: error.message, stack: error.stack }

});

}

}

private async parseWithTemplate(upload: Upload, template: ParsingTemplate): Promise&lt;void&gt; {

_// Use template mappings to parse file_

_// Implementation depends on file type (CSV/Excel/PDF)_

}

private async parseWithHeuristics(upload: Upload, result: HeuristicResult): Promise&lt;void&gt; {

_// Parse using simple heuristics (header detection, column guessing)_

}

private async loadFileBuffer(storageUrl: string): Promise&lt;Buffer&gt; {

_// Load file from storage (local FS or S3)_

return fs.readFile(storageUrl);

}

}

**2.3 Template-Matcher Service**

typescript

_// src/modules/parsing/template-matcher.service.ts_

import { Injectable, Logger } from '@nestjs/common';

import { InjectRepository } from '@nestjs/typeorm';

import { Repository } from 'typeorm';

import { ParsingTemplate } from './entities/parsing-template.entity';

import { Upload } from '../upload/entities/upload.entity';

interface TemplateMatch {

template: ParsingTemplate;

confidence: number;

matchedFeatures: string\[\];

}

@Injectable()

export class TemplateMatcherService {

private readonly logger = new Logger(TemplateMatcherService.name);

constructor(

@InjectRepository(ParsingTemplate)

private readonly templateRepo: Repository&lt;ParsingTemplate&gt;,

) {}

async findMatch(upload: Upload): Promise&lt;TemplateMatch | null&gt; {

_// Load all templates for this tenant + global templates_

const templates = await this.templateRepo.find({

where: \[

{ tenant_id: upload.tenant_id },

{ tenant_id: null } _// Global templates_

\]

});

if (templates.length === 0) {

return null;

}

_// Score each template_

const matches = templates.map(template => {

const confidence = this.calculateMatchConfidence(upload, template);

const features = this.extractMatchedFeatures(upload, template);

return {

template,

confidence,

matchedFeatures: features

};

}).filter(m => m.confidence > 0.5)

.sort((a, b) => b.confidence - a.confidence);

return matches\[0\] || null;

}

private calculateMatchConfidence(upload: Upload, template: ParsingTemplate): number {

let score = 0;

let maxScore = 0;

const detection = template.detection as any;

_// File name pattern_

if (detection.file_name_pattern) {

maxScore += 0.3;

const regex = new RegExp(detection.file_name_pattern, 'i');

if (regex.test(upload.file_name)) {

score += 0.3;

}

}

_// File type match_

if (template.file_type === this.extractFileType(upload.mime_type)) {

score += 0.2;

maxScore += 0.2;

}

_// TODO: Header pattern matching (requires file content)_

_// TODO: Sample hash comparison_

return maxScore > 0 ? score / maxScore : 0;

}

private extractFileType(mimeType: string): string {

if (mimeType.includes('csv') || mimeType.includes('text')) return 'csv';

if (mimeType.includes('excel') || mimeType.includes('spreadsheet')) return 'excel';

if (mimeType.includes('pdf')) return 'pdf';

return 'unknown';

}

private extractMatchedFeatures(upload: Upload, template: ParsingTemplate): string\[\] {

const features: string\[\] = \[\];

const detection = template.detection as any;

if (detection.file_name_pattern) {

const regex = new RegExp(detection.file_name_pattern, 'i');

if (regex.test(upload.file_name)) {

features.push('filename_match');

}

}

return features;

}

}

**Phase 3: Consultant Review UI & Workflow (Wochen 15-20)**

**3.1 Review-Workflow API**

typescript

_// src/modules/upload/upload-review.controller.ts_

import { Controller, Get, Post, Patch, Param, Body } from '@nestjs/common';

import { TenantId } from '../auth/tenant.decorator';

@Controller('api/uploads/:uploadId/review')

export class UploadReviewController {

@Get()

async getReviewData(

@Param('uploadId') uploadId: string,

@TenantId() tenantId: string

) {

_// Load upload with LLM analysis and preview data_

const upload = await this.uploadService.findOne(uploadId, tenantId);

const preview = await this.uploadService.getPreview(uploadId, 50); _// First 50 rows_

return {

success: true,

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

@Body() body: { mappings: ColumnMapping\[\]; save_as_template?: boolean }

) {

_// Consultant accepts LLM's suggestions_

await this.uploadService.applyMappings(uploadId, tenantId, body.mappings);

_// Optionally save as template for future_

if (body.save_as_template) {

await this.templateService.createFromMappings(

uploadId,

tenantId,

body.mappings

);

}

_// Trigger re-parse with confirmed mappings_

await this.parsingQueue.add('parse-with-mappings', {

uploadId,

tenantId,

mappings: body.mappings

});

return { success: true, message: 'Mappings applied, parsing started' };

}

@Patch('correct-mapping')

async correctMapping(

@Param('uploadId') uploadId: string,

@TenantId() tenantId: string,

@Body() body: { column: string; field: string; pattern?: string }

) {

_// Consultant corrects a single mapping_

await this.manualMappingRepo.save({

upload_id: uploadId,

field_name: body.field,

source_column: body.column,

mapping_rule: { type: 'direct', pattern: body.pattern },

created_by: tenantId _// TODO: actual user ID_

});

return { success: true, message: 'Mapping corrected' };

}

@Post('save-as-template')

async saveAsTemplate(

@Param('uploadId') uploadId: string,

@TenantId() tenantId: string,

@Body() body: { template_name: string; description?: string }

) {

const template = await this.templateService.createFromUpload(

uploadId,

tenantId,

body.template_name,

body.description

);

return { success: true, template };

}

}

**3.2 Frontend: Review-UI Konzept**

typescript

_// frontend/src/pages/projects/\[projectId\]/uploads/\[uploadId\]/review.tsx_

interface ReviewPageProps {

upload: Upload;

llmAnalysis: LlmParseResult;

preview: DataPreview;

}

function UploadReviewPage({ upload, llmAnalysis, preview }: ReviewPageProps) {

return (

&lt;div className="review-layout"&gt;

{_/\* Header \*/_}

&lt;div className="review-header"&gt;

&lt;h1&gt;Review: {upload.file_name}&lt;/h1&gt;

&lt;div className="confidence-badge"&gt;

Confidence: {(llmAnalysis.confidence \* 100).toFixed(0)}%

&lt;/div&gt;

&lt;/div&gt;

{_/\* LLM's Interpretation \*/_}

&lt;section className="llm-interpretation"&gt;

&lt;h2&gt;LLM Analysis&lt;/h2&gt;

&lt;p&gt;&lt;strong&gt;File Type:&lt;/strong&gt; {llmAnalysis.file_type}&lt;/p&gt;

&lt;p&gt;&lt;strong&gt;Description:&lt;/strong&gt; {llmAnalysis.description}&lt;/p&gt;

{llmAnalysis.issues.length > 0 && (

&lt;div className="issues-list"&gt;

&lt;h3&gt;Issues Found:&lt;/h3&gt;

&lt;ul&gt;

{llmAnalysis.issues.map((issue, i) => (

&lt;li key={i}&gt;{issue}&lt;/li&gt;

))}

&lt;/ul&gt;

&lt;/div&gt;

)}

&lt;/section&gt;

{_/\* Column Mappings \*/_}

&lt;section className="column-mappings"&gt;

&lt;h2&gt;Suggested Field Mappings&lt;/h2&gt;

&lt;table&gt;

&lt;thead&gt;

&lt;tr&gt;

&lt;th&gt;Column&lt;/th&gt;

&lt;th&gt;Suggested Field&lt;/th&gt;

&lt;th&gt;Confidence&lt;/th&gt;

&lt;th&gt;Sample Values&lt;/th&gt;

&lt;th&gt;Action&lt;/th&gt;

&lt;/tr&gt;

&lt;/thead&gt;

&lt;tbody&gt;

{llmAnalysis.column_mappings?.map((mapping, i) => (

&lt;tr key={i}&gt;

&lt;td&gt;{mapping.column}&lt;/td&gt;

&lt;td&gt;

&lt;select defaultValue={mapping.field}&gt;

&lt;option value="origin_zip"&gt;Origin ZIP&lt;/option&gt;

&lt;option value="dest_zip"&gt;Dest ZIP&lt;/option&gt;

&lt;option value="weight_kg"&gt;Weight (kg)&lt;/option&gt;

{_/\* ... mehr Optionen \*/_}

&lt;/select&gt;

&lt;/td&gt;

&lt;td&gt;

&lt;ConfidenceBadge value={mapping.confidence} /&gt;

&lt;/td&gt;

&lt;td&gt;

&lt;code&gt;{mapping.sample_values.join(', ')}&lt;/code&gt;

&lt;/td&gt;

&lt;td&gt;

&lt;button&gt;✓ Accept&lt;/button&gt;

&lt;button&gt;✗ Reject&lt;/button&gt;

&lt;/td&gt;

&lt;/tr&gt;

))}

&lt;/tbody&gt;

&lt;/table&gt;

&lt;/section&gt;

{_/\* Data Preview \*/_}

&lt;section className="data-preview"&gt;

&lt;h2&gt;Data Preview (First 50 rows)&lt;/h2&gt;

&lt;DataTable data={preview.rows} /&gt;

&lt;/section&gt;

{_/\* Actions \*/_}

&lt;div className="review-actions"&gt;

&lt;button onClick={handleAcceptAll}&gt;

Accept All & Parse

&lt;/button&gt;

&lt;button onClick={handleSaveAsTemplate}&gt;

Save as Template for Future

&lt;/button&gt;

&lt;/div&gt;

&lt;/div&gt;

);

}

**Phase 4: Report-Versioning (Wochen 21-24)**

**4.1 Report-System mit Versioning**

sql

_\-- ============================================_

_\-- REPORT (Versioned)_

_\-- ============================================_

CREATE TABLE report (

id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

project_id UUID NOT NULL REFERENCES project(id),

version INTEGER NOT NULL, _\-- 1, 2, 3..._

report_type VARCHAR(50) NOT NULL,

_\-- 'quick_check' | 'deep_dive' | 'final' | 'monthly'_

title VARCHAR(255),

data_snapshot JSONB NOT NULL,

_\-- Frozen aggregations at time of generation_

_\-- {_

_\-- summary: {...},_

_\-- carriers: \[...\],_

_\-- zones: \[...\],_

_\-- quick_wins: \[...\]_

_\-- }_

data_completeness DECIMAL(3,2), _\-- 0.0-1.0_

_\-- How complete was the data when this report was generated?_

shipment_count INTEGER,

date_range_start DATE,

date_range_end DATE,

generated_by UUID NOT NULL, _\-- Consultant_

generated_at TIMESTAMPTZ DEFAULT now(),

notes TEXT, _\-- Consultant's notes on this version_

UNIQUE(project_id, version)

);

ALTER TABLE report ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON report

USING (project_id IN (SELECT id FROM project WHERE tenant_id = current_setting('app.current_tenant')::UUID));

CREATE INDEX idx_report_project ON report(project_id);

CREATE INDEX idx_report_type ON report(report_type);

typescript

_// src/modules/report/report-versioning.service.ts_

@Injectable()

export class ReportVersioningService {

async generateReport(

projectId: string,

reportType: 'quick_check' | 'deep_dive' | 'final',

tenantId: string

): Promise&lt;Report&gt; {

_// Load all shipments for this project_

const shipments = await this.loadProjectShipments(projectId, tenantId);

_// Calculate data completeness_

const completeness = this.calculateCompleteness(shipments);

_// Generate aggregations_

const dataSnapshot = {

summary: this.calculateSummary(shipments),

carriers: this.aggregateByCarrier(shipments),

zones: this.aggregateByZone(shipments),

weight_classes: this.aggregateByWeightClass(shipments),

quick_wins: this.identifyQuickWins(shipments)

};

_// Get next version number_

const lastReport = await this.reportRepo.findOne({

where: { project_id: projectId },

order: { version: 'DESC' }

});

const version = (lastReport?.version || 0) + 1;

_// Create report_

const report = await this.reportRepo.save({

project_id: projectId,

version,

report_type: reportType,

title: \`\${reportType} Report v\${version}\`,

data_snapshot: dataSnapshot,

data_completeness: completeness,

shipment_count: shipments.length,

date_range_start: this.getMinDate(shipments),

date_range_end: this.getMaxDate(shipments),

generated_by: tenantId, _// TODO: actual user ID_

});

return report;

}

private calculateCompleteness(shipments: Shipment\[\]): number {

if (shipments.length === 0) return 0;

const totalScore = shipments.reduce((sum, s) =>

sum + (s.completeness_score || 0), 0

);

return totalScore / shipments.length;

}

}

**Deployment & Testing**

**Dependencies hinzufügen**

bash

_\# LLM_

npm install --save @anthropic-ai/sdk

_\# PDF parsing_

npm install --save pdf-parse

_\# Excel parsing_

npm install --save xlsx

_\# Utilities_

npm install --save js-yaml

**Environment Variables**

bash

_\# .env_

ANTHROPIC_API_KEY=sk-ant-xxx...

_\# Optional: Cost tracking_

LLM_MAX_COST_PER_MONTH=500 _\# USD_

**Testing-Strategie**

typescript

_// test/e2e/consultant-workflow.spec.ts_

describe('Consultant Workflow E2E', () => {

it('should handle unknown CSV with LLM', async () => {

_// 1. Create project_

const project = await createProject('MECU Quick-Check');

_// 2. Upload unknown CSV_

const upload = await uploadFile(project.id, 'unknown_format.csv');

_// 3. Wait for LLM analysis_

await waitForStatus(upload.id, 'needs_review');

_// 4. Check LLM suggestions_

const review = await getReviewData(upload.id);

expect(review.llm_analysis.confidence).toBeGreaterThan(0.7);

expect(review.suggested_mappings.length).toBeGreaterThan(0);

_// 5. Accept mappings_

await acceptMappings(upload.id, review.suggested_mappings);

_// 6. Check parsing complete_

await waitForStatus(upload.id, 'parsed');

_// 7. Verify shipments created_

const shipments = await getShipments(project.id);

expect(shipments.length).toBeGreaterThan(0);

});

it('should use template for known format', async () => {

_// Template already exists from previous run_

const upload = await uploadFile(project.id, 'mecu_sendungen.xlsx');

_// Should auto-parse without LLM_

await waitForStatus(upload.id, 'parsed');

const uploadData = await getUpload(upload.id);

expect(uploadData.parse_method).toBe('template');

});

});

**Migration Checklist**

**Phase 1: Foundation**

- Create new tables: project, manual_mapping, parsing_template, consultant_note
- Alter upload table with new columns
- Alter shipment table for partial data
- Implement ProjectModule
- Update UploadController to require project_id

**Phase 2: LLM**

- Install Anthropic SDK
- Implement LlmParserService
- Implement TemplateMatcherService
- Implement HeuristicParserService
- Update UploadProcessor with hybrid logic
- Add LLM cost tracking

**Phase 3: Review UI**

- Create UploadReviewController
- Build Frontend: Review page
- Implement manual mapping corrections
- Add "Save as Template" workflow

**Phase 4: Reports**

- Create report table
- Implement ReportVersioningService
- Add report comparison (v1 vs v2)
- Export reports as PDF

**Cost Management**

**LLM Usage ist teuer**, daher:

typescript

_// src/modules/parsing/cost-tracker.service.ts_

@Injectable()

export class CostTrackerService {

async trackLlmCall(

uploadId: string,

inputTokens: number,

outputTokens: number

) {

_// Claude Sonnet pricing (example)_

const costPerInputToken = 0.003 / 1000; _// \$3 per million_

const costPerOutputToken = 0.015 / 1000; _// \$15 per million_

const cost = (inputTokens \* costPerInputToken) +

(outputTokens \* costPerOutputToken);

await this.costLogRepo.save({

upload_id: uploadId,

service: 'anthropic_claude',

input_tokens: inputTokens,

output_tokens: outputTokens,

cost_usd: cost,

created_at: new Date()

});

_// Check monthly limit_

const monthlyTotal = await this.getMonthlyTotal();

if (monthlyTotal > parseFloat(process.env.LLM_MAX_COST_PER_MONTH || '500')) {

this.logger.warn('Monthly LLM budget exceeded!');

_// Send alert_

}

}

}

**Success Metrics**

**Nach Phase 1** (Woche 6):

- Project-Management UI funktioniert
- Upload kann einem Project zugeordnet werden
- Consultant kann Notizen hinzufügen

**Nach Phase 2** (Woche 14):

- LLM kann unbekannte Formate analysieren
- Template-Matching funktioniert für bekannte Formate
- Cost-Tracking aktiv

**Nach Phase 3** (Woche 20):

- Review-UI vollständig
- Consultant kann Mappings korrigieren
- Template-Learning funktioniert (LLM → Template)

**Nach Phase 4** (Woche 24):

- Report v1 (Quick-Check) generierbar
- Report v2 (Deep-Dive) mit mehr Daten
- PDF-Export funktioniert
- **System ist produktionsreif für Berater**

**Next Steps: Self-Service (Phase 5+)**

Erst wenn Berater-Tool läuft, dann:

- Client Portal für Kunden
- Self-Service Upload
- Automated Monitoring
- Alert-System

Aber das ist **nicht MVP**. MVP = Berater-Tool funktioniert