import { Processor, Process } from '@nestjs/bull';
import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Job } from 'bull';
import { Upload } from './entities/upload.entity';
import { LlmParserService } from '../parsing/services/llm-parser.service';
import { TemplateMatcherService } from '../parsing/services/template-matcher.service';
import { UploadService } from './upload.service';

/**
 * Job data for file parsing
 */
interface ParseFileJobData {
  uploadId: string;
  projectId: string;
  tenantId: string;
}

/**
 * UploadProcessorV2 - Hybrid parsing with templates + LLM
 *
 * New processor that implements the hybrid parsing strategy:
 * 1. Try template matching first (fast, deterministic)
 * 2. Fall back to LLM analysis if no template matches
 * 3. Mark for consultant review if confidence is low
 */
@Processor('upload')
@Injectable()
export class UploadProcessorV2 {
  private readonly logger = new Logger(UploadProcessorV2.name);

  constructor(
    @InjectRepository(Upload)
    private readonly uploadRepo: Repository<Upload>,
    private readonly llmParser: LlmParserService,
    private readonly templateMatcher: TemplateMatcherService,
    private readonly uploadService: UploadService,
  ) {}

  /**
   * Main file parsing job handler
   */
  @Process('parse-file-v2')
  async handleParseFile(job: Job<ParseFileJobData>): Promise<void> {
    const { uploadId, projectId, tenantId } = job.data;

    this.logger.log({
      event: 'parse_file_start',
      upload_id: uploadId,
      project_id: projectId,
      tenant_id: tenantId,
    });

    try {
      // Load upload record
      const upload = await this.uploadRepo.findOne({
        where: { id: uploadId, tenant_id: tenantId },
      });

      if (!upload) {
        throw new Error(`Upload ${uploadId} not found`);
      }

      // Update status to processing
      await this.uploadRepo.update(uploadId, {
        status: 'processing',
      });

      // Load file content
      const fileBuffer = await this.uploadService.loadFile(upload.storage_url);

      // STEP 1: Try template matching
      const templateMatch = await this.templateMatcher.findMatch(
        upload,
        tenantId,
        fileBuffer.toString('utf-8', 0, 5000) // First 5KB for analysis
      );

      if (templateMatch && templateMatch.confidence > 0.9) {
        this.logger.log({
          event: 'template_match_success',
          upload_id: uploadId,
          template_id: templateMatch.template.id,
          template_name: templateMatch.template.name,
          confidence: templateMatch.confidence,
        });

        // Use template-based parsing
        await this.parseWithTemplate(
          upload,
          templateMatch.template,
          fileBuffer,
          tenantId
        );

        await this.uploadRepo.update(uploadId, {
          status: 'parsed',
          parse_method: 'template',
          confidence: templateMatch.confidence,
        });

        this.logger.log({
          event: 'parse_file_complete',
          upload_id: uploadId,
          method: 'template',
        });

        return;
      }

      // STEP 2: No good template match → Use LLM
      this.logger.log({
        event: 'fallback_to_llm',
        upload_id: uploadId,
        template_confidence: templateMatch?.confidence || 0,
      });

      if (!this.llmParser.isAvailable()) {
        throw new Error('LLM parser not available (ANTHROPIC_API_KEY not set)');
      }

      const llmResult = await this.llmParser.analyzeFile(
        fileBuffer,
        {
          filename: upload.filename,
          mime_type: upload.mime_type,
        }
      );

      // Save LLM analysis
      await this.uploadRepo.update(uploadId, {
        status: llmResult.needs_review ? 'needs_review' : 'parsed',
        parse_method: 'llm',
        confidence: llmResult.confidence,
        suggested_mappings: llmResult.column_mappings as any,
        llm_analysis: llmResult as any,
        parsing_issues: llmResult.issues as any,
      });

      this.logger.log({
        event: 'parse_file_complete',
        upload_id: uploadId,
        method: 'llm',
        confidence: llmResult.confidence,
        needs_review: llmResult.needs_review,
        issues_count: llmResult.issues.length,
      });

    } catch (error) {
      this.logger.error({
        event: 'parse_file_error',
        upload_id: uploadId,
        error: error.message,
        stack: error.stack,
      });

      await this.uploadRepo.update(uploadId, {
        status: 'error',
        parse_errors: {
          message: error.message,
          stack: error.stack,
          timestamp: new Date().toISOString(),
        },
      });

      throw error;
    }
  }

  /**
   * Parse file using a template
   */
  private async parseWithTemplate(
    upload: Upload,
    template: any,
    fileBuffer: Buffer,
    tenantId: string
  ): Promise<void> {
    this.logger.log({
      event: 'parse_with_template_start',
      upload_id: upload.id,
      template_category: template.template_category,
    });

    // Route to appropriate parser based on template category
    switch (template.template_category) {
      case 'shipment_list':
        await this.parseShipmentList(fileBuffer, template, upload.project_id, tenantId);
        break;

      case 'invoice':
        await this.parseInvoice(fileBuffer, template, upload.project_id, tenantId);
        break;

      case 'tariff':
        await this.parseTariff(fileBuffer, template, tenantId);
        break;

      default:
        this.logger.warn({
          event: 'unknown_template_category',
          category: template.template_category,
        });
        throw new Error(`Unknown template category: ${template.template_category}`);
    }
  }

  /**
   * Parse shipment list CSV/Excel
   */
  private async parseShipmentList(
    buffer: Buffer,
    template: any,
    projectId: string,
    tenantId: string
  ): Promise<void> {
    this.logger.log({
      event: 'parse_shipment_list',
      project_id: projectId,
      template_name: template.name,
    });

    // TODO: Implement CSV/Excel parsing using template mappings
    // This will use the mappings from template.mappings to extract fields
    // For now, log that this is not yet implemented

    this.logger.warn('Shipment list parsing not yet implemented in V2 processor');
  }

  /**
   * Parse invoice PDF
   */
  private async parseInvoice(
    buffer: Buffer,
    template: any,
    projectId: string,
    tenantId: string
  ): Promise<void> {
    this.logger.log({
      event: 'parse_invoice',
      project_id: projectId,
      template_name: template.name,
    });

    // TODO: Implement invoice parsing
    this.logger.warn('Invoice parsing not yet implemented in V2 processor');
  }

  /**
   * Parse tariff table PDF
   */
  private async parseTariff(
    buffer: Buffer,
    template: any,
    tenantId: string
  ): Promise<void> {
    this.logger.log({
      event: 'parse_tariff',
      tenant_id: tenantId,
      template_name: template.name,
    });

    // TODO: Implement tariff parsing
    this.logger.warn('Tariff parsing not yet implemented in V2 processor');
  }
}
