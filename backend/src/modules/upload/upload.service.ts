import { Injectable, Logger, ConflictException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { InjectQueue } from '@nestjs/bull';
import { Repository } from 'typeorm';
import { Queue } from 'bull';
import * as crypto from 'crypto';
import * as fs from 'fs/promises';
import * as path from 'path';
import { Upload } from './entities/upload.entity';

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
    @InjectQueue('upload')
    private readonly uploadQueue: Queue,
  ) {}

  async uploadFile(
    file: Express.Multer.File,
    tenantId: string,
    sourceType: string,
  ): Promise<UploadResult> {
    const fileHash = this.calculateFileHash(file.buffer);
    
    const existingUpload = await this.uploadRepository.findOne({
      where: { 
        tenant_id: tenantId, 
        file_hash: fileHash 
      },
    });

    if (existingUpload) {
      this.logger.log(
        `File already exists for tenant ${tenantId}: ${existingUpload.filename} (hash: ${fileHash})`,
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
        status: 'pending',
      });

      const savedUpload = await this.uploadRepository.save(upload);

      this.logger.log(
        `File uploaded successfully for tenant ${tenantId}: ${file.originalname} -> ${storagePath}`,
      );

      // Enqueue CSV parsing job for new uploads
      if (file.mimetype === 'text/csv' || file.originalname.toLowerCase().endsWith('.csv')) {
        await this.uploadQueue.add('parse-csv', {
          uploadId: savedUpload.id,
          tenantId: savedUpload.tenant_id,
        });

        this.logger.log(
          `Enqueued CSV parsing job for upload ${savedUpload.id}`,
        );
      }

      return {
        upload: savedUpload,
        alreadyProcessed: false,
      };
    } catch (error) {
      this.logger.error(
        `Failed to upload file for tenant ${tenantId}: ${file.originalname}`,
        (error as Error).stack,
      );

      try {
        await fs.unlink(storagePath);
      } catch (cleanupError) {
        this.logger.warn(
          `Failed to cleanup file after upload error: ${storagePath}`,
          (cleanupError as Error).stack,
        );
      }

      if ((error as any).code === '23505') {
        throw new ConflictException(
          'File with the same hash already exists for this tenant',
        );
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
        tenant_id: tenantId 
      },
    });
  }

  async updateStatus(
    id: string,
    tenantId: string,
    status: string,
    parseErrors?: any,
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
    lines: number = 50,
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
  async getShipments(uploadId: string, tenantId: string): Promise<any[]> {
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
    mappings: Record<string, string>,
  ): Promise<void> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    // Update suggested mappings
    await this.uploadRepository.update(uploadId, {
      suggested_mappings: mappings as any,
      status: 'pending',
    });

    // Re-queue for parsing
    await this.uploadQueue.add('parse-csv', {
      uploadId,
      tenantId,
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
    notes?: string,
  ): Promise<void> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    await this.uploadRepository.update(uploadId, {
      status: 'reviewed',
      meta: {
        ...upload.meta,
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
    options: { reason?: string; force_llm?: boolean },
  ): Promise<void> {
    const upload = await this.findById(uploadId, tenantId);
    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    // Update status
    await this.uploadRepository.update(uploadId, {
      status: 'pending',
      meta: {
        ...upload.meta,
        reprocess_reason: options.reason,
        reprocess_requested_at: new Date().toISOString(),
      },
    });

    // Re-queue
    await this.uploadQueue.add('parse-csv', {
      uploadId,
      tenantId,
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
    tenantId: string,
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

    // TODO: Implement proper quality metrics calculation
    // This requires loading shipments and analyzing their completeness
    this.logger.warn('getQualityMetrics not fully implemented');

    return {
      completeness: upload.confidence || 0,
      missing_fields: [],
      data_issues: upload.parsing_issues || [],
      total_rows: 0,
      valid_rows: 0,
    };
  }
}