import { Processor, Process } from '@nestjs/bull';
import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Job } from 'bull';
import { Upload } from './entities/upload.entity';
import { CarrierAlias } from './entities/carrier-alias.entity';
import { Shipment } from '../parsing/entities/shipment.entity';
import { CsvParserService } from '../parsing/csv-parser.service';
import { TariffEngineService } from '../tariff/tariff-engine.service';
import { UploadService } from './upload.service';

interface ParseCsvJobData {
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
  ) {}

  @Process('parse-csv')
  async handleParseCsv(job: Job<ParseCsvJobData>): Promise<void> {
    const { uploadId, tenantId } = job.data;

    this.logger.log(`Starting CSV processing for upload ${uploadId}, tenant ${tenantId}`);

    try {
      // 1. Get upload from database
      const upload = await this.uploadRepository.findOne({
        where: { id: uploadId, tenant_id: tenantId },
      });

      if (!upload) {
        throw new Error(`Upload ${uploadId} not found for tenant ${tenantId}`);
      }

      if (!upload.storage_url) {
        throw new Error(`Upload ${uploadId} has no storage URL`);
      }

      // 2. Call csvParser.parse to get shipments
      const parsedShipments = await this.csvParserService.parse(
        upload.storage_url,
        tenantId,
        uploadId,
      );

      this.logger.log(`Parsed ${parsedShipments.length} shipments from upload ${uploadId}`);

      // 3. Process each shipment: map carrier name → carrier_id and save
      const processedShipments: Shipment[] = [];
      
      for (const shipment of parsedShipments) {
        try {
          // Map carrier name to carrier_id via carrier_alias
          if (shipment.carrier_name && !shipment.carrier_id) {
            const carrierId = await this.mapCarrierNameToId(
              shipment.carrier_name,
              tenantId,
            );
            
            if (carrierId) {
              shipment.carrier_id = carrierId;
              this.logger.debug(
                `Mapped carrier "${shipment.carrier_name}" to ${carrierId} for shipment ${shipment.id || 'new'}`,
              );
            } else {
              this.logger.warn(
                `No carrier mapping found for "${shipment.carrier_name}" in tenant ${tenantId}`,
              );
            }
          }

          // Save shipment to database
          const savedShipment = await this.shipmentRepository.save(shipment);
          processedShipments.push(savedShipment);

        } catch (error) {
          this.logger.error(
            `Error processing shipment: ${(error as Error).message}`,
            (error as Error).stack,
          );
          // Continue processing other shipments
        }
      }

      this.logger.log(`Saved ${processedShipments.length} shipments to database`);

      // 4. Calculate benchmarks for each shipment
      let benchmarkCount = 0;
      
      for (const shipment of processedShipments) {
        try {
          if (shipment.carrier_id) {
            // Only calculate benchmarks for shipments with valid carrier mapping
            await this.tariffEngineService.calculateExpectedCost(shipment);
            benchmarkCount++;
            
            this.logger.debug(
              `Calculated benchmark for shipment ${shipment.id}`,
            );
          } else {
            this.logger.warn(
              `Skipping benchmark calculation for shipment ${shipment.id} - no carrier_id`,
            );
          }
        } catch (error) {
          this.logger.error(
            `Error calculating benchmark for shipment ${shipment.id}: ${(error as Error).message}`,
            (error as Error).stack,
          );
          // Continue processing other shipments
        }
      }

      this.logger.log(`Calculated ${benchmarkCount} benchmarks for upload ${uploadId}`);

      // 5. Update upload.status = 'parsed'
      await this.uploadService.updateStatus(uploadId, tenantId, 'parsed');

      this.logger.log(`Successfully completed processing upload ${uploadId}`);

    } catch (error) {
      this.logger.error(
        `Error processing upload ${uploadId}: ${(error as Error).message}`,
        (error as Error).stack,
      );

      // 6. On error: update upload.status = 'failed', parse_errors = error
      await this.uploadService.updateStatus(
        uploadId,
        tenantId,
        'failed',
        {
          message: (error as Error).message,
          stack: (error as Error).stack,
          timestamp: new Date().toISOString(),
        },
      );

      throw error; // Re-throw so the job is marked as failed
    }
  }

  private async mapCarrierNameToId(
    carrierName: string,
    tenantId: string,
  ): Promise<string | null> {
    try {
      // Look for tenant-specific alias first, then global fallback
      const alias = await this.carrierAliasRepository.findOne({
        where: [
          {
            tenant_id: tenantId,
            alias_text: carrierName,
          },
          {
            tenant_id: null, // Global alias
            alias_text: carrierName,
          },
        ],
      });

      return alias?.carrier_id || null;
    } catch (error) {
      this.logger.error(
        `Error mapping carrier name "${carrierName}": ${(error as Error).message}`,
        (error as Error).stack,
      );
      return null;
    }
  }
}