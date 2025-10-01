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
}