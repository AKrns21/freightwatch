import { Processor, Process } from '@nestjs/bull';
import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository , IsNull} from 'typeorm';
import { Job } from 'bull';
import { Upload } from './entities/upload.entity';
import { CarrierAlias } from './entities/carrier-alias.entity';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';
import { CsvParserService } from '@/modules/parsing/csv-parser.service';
import { TariffEngineService } from '@/modules/tariff/tariff-engine.service';
import { UploadService } from './upload.service';
import { TemplateMatcherService } from '@/modules/parsing/services/template-matcher.service';
import { LlmParserService } from '@/modules/parsing/services/llm-parser.service';
import { ParsingTemplate } from '@/modules/parsing/entities/parsing-template.entity';

/**
 * UploadProcessor - Phase 3 Refactored
 *
 * Hybrid Approach:
 * 1. Try Template Matching (fast, deterministic)
 * 2. Fall back to LLM Analysis (flexible, learns from corrections)
 * 3. Manual Review if needed (low confidence or errors)
 */

interface ParseFileJobData {
  uploadId: string;
  tenantId: string;
}

@Processor('upload')
@Injectable()
export class UploadProcessor {
  private readonly logger = new Logger(UploadProcessor.name);

  constructor(
    @InjectRepository(Upload)
    private readonly uploadRepository: Repository<Upload>,
    @InjectRepository(CarrierAlias)
    private readonly carrierAliasRepository: Repository<CarrierAlias>,
    @InjectRepository(Shipment)
    private readonly shipmentRepository: Repository<Shipment>,
    private readonly csvParserService: CsvParserService,
    private readonly tariffEngineService: TariffEngineService,
    private readonly uploadService: UploadService,
    private readonly templateMatcher: TemplateMatcherService,
    private readonly llmParser: LlmParserService,
  ) {}

  @Process('parse-file')
  async handleParseFile(job: Job<ParseFileJobData>): Promise<void> {
    const { uploadId, tenantId } = job.data;

    this.logger.log({
      event: 'upload_processing_start',
      upload_id: uploadId,
      tenant_id: tenantId,
    });

    try {
      const upload = await this.uploadRepository.findOne({
        where: { id: uploadId, tenant_id: tenantId },
      });

      if (!upload) {
        throw new Error(`Upload ${uploadId} not found`);
      }

      // Step 1: Try Template Matching
      this.logger.log({
        event: 'template_matching_start',
        upload_id: uploadId,
      });

      const templateMatch = await this.templateMatcher.findMatch(upload, tenantId);

      if (templateMatch && templateMatch.confidence >= 0.8) {
        this.logger.log({
          event: 'template_match_found',
          upload_id: uploadId,
          template_id: templateMatch.template.id,
          template_name: templateMatch.template.name,
          confidence: templateMatch.confidence,
        });

        // Parse with template
        await this.parseWithTemplate(upload, templateMatch.template, tenantId);

        await this.uploadRepository.update(uploadId, {
          status: 'parsed',
          parse_method: 'template',
          confidence: templateMatch.confidence,
        });

        this.logger.log({
          event: 'upload_processing_complete',
          upload_id: uploadId,
          parse_method: 'template',
        });

        return;
      }

      // Step 2: Fall back to LLM analysis
      if (!this.llmParser.isAvailable()) {
        this.logger.warn({
          event: 'llm_not_available',
          upload_id: uploadId,
          message: 'No template match and LLM not available',
        });

        await this.uploadRepository.update(uploadId, {
          status: 'needs_manual_review',
          parse_method: 'manual',
          parsing_issues: [{
            type: 'no_template_match',
            message: 'No matching template found and LLM not configured',
            timestamp: new Date(),
          }],
        });

        return;
      }

      this.logger.log({
        event: 'llm_analysis_start',
        upload_id: uploadId,
      });

      const fileBuffer = await this.uploadService.loadFile(upload.storage_url);
      const llmResult = await this.llmParser.analyzeFile(
        fileBuffer,
        {
          filename: upload.filename,
          mime_type: upload.mime_type,
          content_preview: '',
        } as any
      );

      await this.uploadRepository.update(uploadId, {
        status: llmResult.needs_review ? 'needs_review' : 'parsed',
        parse_method: 'llm',
        confidence: llmResult.confidence,
        llm_analysis: llmResult as any,
        suggested_mappings: llmResult.column_mappings,
        parsing_issues: llmResult.issues,
      });

      this.logger.log({
        event: 'llm_analysis_complete',
        upload_id: uploadId,
        confidence: llmResult.confidence,
        needs_review: llmResult.needs_review,
      });

      // If LLM is confident enough, parse automatically
      if (!llmResult.needs_review && llmResult.confidence >= 0.7) {
        await this.parseWithLlmMappings(upload, llmResult.column_mappings, tenantId);
      }

    } catch (error) {
      this.logger.error({
        event: 'upload_processing_error',
        upload_id: uploadId,
        error: (error as Error).message,
        stack: (error as Error).stack,
      });

      await this.uploadRepository.update(uploadId, {
        status: 'error',
        parse_errors: {
          message: (error as Error).message,
          stack: (error as Error).stack,
          timestamp: new Date().toISOString(),
        } as any,
      });

      throw error;
    }
  }

  /**
   * Parse file using a template
   */
  private async parseWithTemplate(
    upload: Upload,
    template: ParsingTemplate,
    tenantId: string,
  ): Promise<void> {
    this.logger.log({
      event: 'parsing_with_template',
      upload_id: upload.id,
      template_id: template.id,
    });

    // For CSV/Excel files
    if (upload.mime_type?.includes('csv') || upload.mime_type?.includes('excel')) {
      const shipments = await this.csvParserService.parseWithTemplate(
        upload,
        template,
      );

      await this.saveShipments(shipments, tenantId);
      await this.calculateBenchmarks(shipments);
    }
    // TODO: Add support for PDF and other formats
  }

  /**
   * Parse file using LLM-suggested mappings
   */
  private async parseWithLlmMappings(
    upload: Upload,
    mappings: any[],
    tenantId: string,
  ): Promise<void> {
    this.logger.log({
      event: 'parsing_with_llm_mappings',
      upload_id: upload.id,
    });

    // Create temporary template from LLM mappings
    const tempTemplate: Partial<ParsingTemplate> = {
      name: `LLM-generated for ${upload.filename}`,
      file_type: 'csv',
      mappings: mappings.reduce((acc, m) => {
        acc[m.field] = {
          keywords: [m.column],
          column: m.column,
        };
        return acc;
      }, {}),
    };

    const shipments = await this.csvParserService.parseWithTemplate(
      upload,
      tempTemplate as ParsingTemplate,
    );

    await this.saveShipments(shipments, tenantId);
    await this.calculateBenchmarks(shipments);
  }

  /**
   * Save shipments to database with carrier mapping
   */
  private async saveShipments(
    shipments: Shipment[],
    tenantId: string,
  ): Promise<void> {
    const processedShipments: Shipment[] = [];

    for (const shipment of shipments) {
      try {
        // Map carrier name to carrier_id
        const carrierName = (shipment as any).carrier_name;
        if (carrierName && !shipment.carrier_id) {
          const carrierId = await this.mapCarrierNameToId(
            carrierName,
            tenantId,
          );

          if (carrierId) {
            shipment.carrier_id = carrierId;
          } else {
            this.logger.warn({
              event: 'carrier_mapping_failed',
              carrier_name: carrierName,
              tenant_id: tenantId,
            });
          }
        }

        const savedShipment = await this.shipmentRepository.save(shipment);
        processedShipments.push(savedShipment);
      } catch (error) {
        this.logger.error({
          event: 'shipment_save_error',
          error: (error as Error).message,
        });
      }
    }

    this.logger.log({
      event: 'shipments_saved',
      count: processedShipments.length,
    });
  }

  /**
   * Calculate benchmarks for shipments
   */
  private async calculateBenchmarks(shipments: Shipment[]): Promise<void> {
    let benchmarkCount = 0;

    for (const shipment of shipments) {
      try {
        if (shipment.carrier_id) {
          await this.tariffEngineService.calculateExpectedCost(shipment);
          benchmarkCount++;
        }
      } catch (error) {
        this.logger.error({
          event: 'benchmark_calculation_error',
          shipment_id: shipment.id,
          error: (error as Error).message,
        });
      }
    }

    this.logger.log({
      event: 'benchmarks_calculated',
      count: benchmarkCount,
    });
  }

  /**
   * Map carrier name to carrier ID using carrier_alias table
   */
  private async mapCarrierNameToId(
    carrierName: string,
    tenantId: string,
  ): Promise<string | null> {
    try {
      const alias = await this.carrierAliasRepository.findOne({
        where: [
          { tenant_id: tenantId, alias_text: carrierName },
          { tenant_id: IsNull(), alias_text: carrierName }, // Global fallback
        ],
      });

      return alias?.carrier_id || null;
    } catch (error) {
      this.logger.error({
        event: 'carrier_mapping_error',
        carrier_name: carrierName,
        error: (error as Error).message,
      });
      return null;
    }
  }

  // Keep legacy handler for backwards compatibility
  @Process('parse-csv')
  async handleParseCsv(job: Job<ParseFileJobData>): Promise<void> {
    return this.handleParseFile(job);
  }
}
