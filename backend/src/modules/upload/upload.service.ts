import { Injectable, Logger, ConflictException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { InjectQueue } from '@nestjs/bull';
import { Repository } from 'typeorm';
import { Queue } from 'bull';
import * as crypto from 'crypto';
import * as fs from 'fs/promises';
import * as path from 'path';
import { Upload, UploadStatus } from './entities/upload.entity';
import { Carrier } from './entities/carrier.entity';
import { CarrierAlias } from './entities/carrier-alias.entity';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';

interface UploadResult {
  upload: Upload;
  alreadyProcessed: boolean;
}

@Injectable()
export class UploadService {
  private readonly logger = new Logger(UploadService.name);
  private readonly uploadsDir = 'uploads';

  constructor(
    @InjectRepository(Upload)
    private readonly uploadRepository: Repository<Upload>,
    @InjectRepository(Shipment)
    private readonly shipmentRepository: Repository<Shipment>,
    @InjectRepository(Carrier)
    private readonly carrierRepository: Repository<Carrier>,
    @InjectRepository(CarrierAlias)
    private readonly carrierAliasRepository: Repository<CarrierAlias>,
    @InjectQueue('upload')
    private readonly uploadQueue: Queue
  ) {}

  async uploadFile(
    file: Express.Multer.File,
    tenantId: string,
    sourceType: string
  ): Promise<UploadResult> {
    const fileHash = this.calculateFileHash(file.buffer);

    const existingUpload = await this.uploadRepository.findOne({
      where: {
        tenant_id: tenantId,
        file_hash: fileHash,
      },
    });

    if (existingUpload) {
      this.logger.log(
        `File already exists for tenant ${tenantId}: ${existingUpload.filename} (hash: ${fileHash})`
      );
      return {
        upload: existingUpload,
        alreadyProcessed: true,
      };
    }

    const fileExtension = path.extname(file.originalname);
    const storageFileName = `${fileHash}${fileExtension}`;
    const tenantDir = path.join(this.uploadsDir, tenantId);
    const storagePath = path.join(tenantDir, storageFileName);

    try {
      await fs.mkdir(tenantDir, { recursive: true });
      await fs.writeFile(storagePath, file.buffer);

      const upload = this.uploadRepository.create({
        tenant_id: tenantId,
        filename: file.originalname,
        file_hash: fileHash,
        mime_type: file.mimetype,
        source_type: sourceType,
        storage_url: storagePath,
        status: UploadStatus.PENDING,
      });

      const savedUpload = await this.uploadRepository.save(upload);

      this.logger.log(
        `File uploaded successfully for tenant ${tenantId}: ${file.originalname} -> ${storagePath}`
      );

      // Enqueue CSV parsing job for new uploads
      if (file.mimetype === 'text/csv' || file.originalname.toLowerCase().endsWith('.csv')) {
        await this.uploadQueue.add('parse-csv', {
          uploadId: savedUpload.id,
          tenantId: savedUpload.tenant_id,
          sourceType: savedUpload.source_type,
        });

        this.logger.log(`Enqueued CSV parsing job for upload ${savedUpload.id}`);
      }

      return {
        upload: savedUpload,
        alreadyProcessed: false,
      };
    } catch (error) {
      this.logger.error(
        `Failed to upload file for tenant ${tenantId}: ${file.originalname}`,
        (error as Error).stack
      );

      try {
        await fs.unlink(storagePath);
      } catch (cleanupError) {
        this.logger.warn(
          `Failed to cleanup file after upload error: ${storagePath}`,
          (cleanupError as Error).stack
        );
      }

      if ((error as any).code === '23505') {
        throw new ConflictException('File with the same hash already exists for this tenant');
      }

      throw error;
    }
  }

  private calculateFileHash(buffer: Buffer): string {
    return crypto.createHash('sha256').update(buffer).digest('hex');
  }

  /**
   * Load file from storage
   */
  async loadFile(storagePath: string): Promise<Buffer> {
    try {
      return await fs.readFile(storagePath);
    } catch (error) {
      this.logger.error(`Failed to load file from ${storagePath}`, error);
      throw error;
    }
  }

  async findByTenant(tenantId: string): Promise<Upload[]> {
    return this.uploadRepository.find({
      where: { tenant_id: tenantId },
      order: { received_at: 'DESC' },
    });
  }

  async findById(id: string, tenantId: string): Promise<Upload | null> {
    return this.uploadRepository.findOne({
      where: {
        id,
        tenant_id: tenantId,
      },
    });
  }

  async updateStatus(
    id: string,
    tenantId: string,
    status: UploadStatus,
    parseErrors?: any
  ): Promise<Upload | null> {
    const upload = await this.findById(id, tenantId);
    if (!upload) {
      return null;
    }

    upload.status = status;
    if (parseErrors !== undefined) {
      upload.parse_errors = parseErrors;
    }

    return this.uploadRepository.save(upload);
  }

  /**
   * Get file preview (first N lines)
   */
  async getPreview(
    uploadId: string,
    tenantId: string,
    lines: number = 50
  ): Promise<{ lines: string[]; total_lines: number }> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    try {
      const fileBuffer = await this.loadFile(upload.storage_url);
      const content = fileBuffer.toString('utf-8');
      const allLines = content.split('\n');

      return {
        lines: allLines.slice(0, lines),
        total_lines: allLines.length,
      };
    } catch (error) {
      this.logger.error(`Failed to get preview for ${uploadId}`, error);
      throw error;
    }
  }

  /**
   * Get shipments for an upload
   */
  async getShipments(_uploadId: string, _tenantId: string): Promise<any[]> {
    // TODO: Import Shipment entity and use proper repository
    // For now, return empty array as placeholder
    this.logger.warn('getShipments not yet implemented');
    return [];
  }

  /**
   * Apply corrected mappings and re-parse
   */
  async applyMappings(
    uploadId: string,
    tenantId: string,
    mappings: Record<string, string>
  ): Promise<void> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    // Update suggested mappings
    await this.uploadRepository.update(uploadId, {
      suggested_mappings: mappings as any,
      status: UploadStatus.PENDING,
    });

    // Re-queue for parsing
    await this.uploadQueue.add('parse-csv', {
      uploadId,
      tenantId,
      sourceType: upload.source_type,
      forceMappings: mappings,
    });

    this.logger.log({
      event: 'mappings_applied',
      upload_id: uploadId,
      mapping_count: Object.keys(mappings).length,
    });
  }

  /**
   * Mark upload as reviewed by consultant
   */
  async markAsReviewed(
    uploadId: string,
    tenantId: string,
    userId: string,
    notes?: string
  ): Promise<void> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    await this.uploadRepository.update(uploadId, {
      status: UploadStatus.REVIEWED,
      meta: {
        ...(upload.meta as Record<string, unknown>),
        reviewed_by: userId,
        reviewed_at: new Date().toISOString(),
        review_notes: notes,
      },
    });

    this.logger.log({
      event: 'upload_reviewed',
      upload_id: uploadId,
      reviewed_by: userId,
    });
  }

  /**
   * Queue upload for re-processing
   */
  async reprocess(
    uploadId: string,
    tenantId: string,
    options: { reason?: string; force_llm?: boolean }
  ): Promise<void> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    // Update status
    await this.uploadRepository.update(uploadId, {
      status: UploadStatus.PENDING,
      meta: {
        ...(upload.meta as Record<string, unknown>),
        reprocess_reason: options.reason,
        reprocess_requested_at: new Date().toISOString(),
      },
    });

    // Re-queue
    await this.uploadQueue.add('parse-csv', {
      uploadId,
      tenantId,
      sourceType: upload.source_type,
      forceLlm: options.force_llm,
    });

    this.logger.log({
      event: 'upload_requeued',
      upload_id: uploadId,
      reason: options.reason,
      force_llm: options.force_llm,
    });
  }

  /**
   * Get data quality metrics for upload
   */
  async getQualityMetrics(
    uploadId: string,
    tenantId: string
  ): Promise<{
    completeness: number;
    missing_fields: string[];
    data_issues: any[];
    total_rows: number;
    valid_rows: number;
  }> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    const shipments = await this.shipmentRepository.find({
      where: { upload_id: uploadId, tenant_id: tenantId },
      select: ['id', 'completeness_score', 'missing_fields', 'data_quality_issues'],
    });

    const total_rows = shipments.length;

    if (total_rows === 0) {
      return {
        completeness: upload.confidence || 0,
        missing_fields: [],
        data_issues: (upload.parsing_issues as unknown[]) || [],
        total_rows: 0,
        valid_rows: 0,
      };
    }

    const completeness =
      shipments.reduce((sum, s) => sum + (s.completeness_score || 0), 0) / total_rows;

    const valid_rows = shipments.filter((s) => (s.completeness_score || 0) >= 0.7).length;

    const missing_fields = [
      ...new Set(shipments.flatMap((s) => (s.missing_fields as string[]) || [])),
    ];

    const data_issues = shipments.flatMap((s) => (s.data_quality_issues as unknown[]) || []);

    return {
      completeness,
      missing_fields,
      data_issues,
      total_rows,
      valid_rows,
    };
  }

  /**
   * List non-placeholder carriers available for the tenant (for alias resolution dropdown)
   */
  async listCarriers(tenantId: string): Promise<Carrier[]> {
    return this.carrierRepository
      .createQueryBuilder('c')
      .where(
        "(c.tenant_id = :tenantId OR c.tenant_id IS NULL) AND c.code_norm NOT LIKE 'PLACEHOLDER_%'",
        { tenantId }
      )
      .orderBy('c.name', 'ASC')
      .getMany();
  }

  /**
   * Resolve an unmapped carrier: create a real alias and re-assign affected shipments.
   *
   * Steps:
   * 1. Find or create an alias mapping carrierName → realCarrierId
   * 2. Re-point all shipments in the upload that currently hold the placeholder carrier
   * 3. Remove the resolved unknown_carrier entry from upload.parsing_issues
   */
  async resolveCarrier(
    uploadId: string,
    tenantId: string,
    carrierName: string,
    realCarrierId: string
  ): Promise<void> {
    // 1. Find placeholder carrier (if any) so we can re-assign shipments
    const placeholderCodeNorm = `PLACEHOLDER_${carrierName.toUpperCase().replace(/[^A-Z0-9]/g, '_').substring(0, 40)}`;
    const placeholder = await this.carrierRepository.findOne({
      where: { code_norm: placeholderCodeNorm },
    });

    // 2. Upsert tenant-scoped alias carrierName → realCarrierId
    const existing = await this.carrierAliasRepository.findOne({
      where: { tenant_id: tenantId, alias_text: carrierName },
    });
    if (existing) {
      await this.carrierAliasRepository.update(existing.id, { carrier_id: realCarrierId });
    } else {
      await this.carrierAliasRepository.save(
        this.carrierAliasRepository.create({
          tenant_id: tenantId,
          alias_text: carrierName,
          carrier_id: realCarrierId,
        })
      );
    }

    // 3. Re-assign shipments that reference the placeholder
    if (placeholder) {
      await this.shipmentRepository.update(
        { upload_id: uploadId, tenant_id: tenantId, carrier_id: placeholder.id },
        { carrier_id: realCarrierId }
      );
    }

    // 4. Remove resolved entry from parsing_issues
    const upload = await this.findById(uploadId, tenantId);
    if (upload) {
      const remaining = ((upload.parsing_issues as unknown[]) ?? []).filter(
        (issue: any) => !(issue.type === 'unknown_carrier' && issue.carrier_name === carrierName)
      );
      await this.uploadRepository.update(uploadId, { parsing_issues: remaining });
    }

    this.logger.log({
      event: 'carrier_resolved',
      upload_id: uploadId,
      carrier_name: carrierName,
      real_carrier_id: realCarrierId,
    });
  }
}
