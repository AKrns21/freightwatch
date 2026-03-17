import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { BullModule } from '@nestjs/bull';
import { Upload } from './entities/upload.entity';
import { Carrier } from './entities/carrier.entity';
import { CarrierAlias } from './entities/carrier-alias.entity';
import { RawExtraction } from './entities/raw-extraction.entity';
import { ExtractionCorrection } from './entities/extraction-correction.entity';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';
import { UploadService } from './upload.service';
import { UploadController } from './upload.controller';
import { UploadReviewController } from './upload-review.controller';
import { UploadProcessor } from './upload-processor.service';
import { DocumentClassifierService } from './document-classifier.service';
import { ParsingModule } from '@/modules/parsing/parsing.module';
import { TariffModule } from '@/modules/tariff/tariff.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([Upload, Carrier, CarrierAlias, Shipment, RawExtraction, ExtractionCorrection]),
    BullModule.registerQueue({
      name: 'upload',
    }),
    ParsingModule,
    TariffModule,
  ],
  controllers: [UploadController, UploadReviewController],
  providers: [UploadService, UploadProcessor, DocumentClassifierService],
  exports: [UploadService],
})
export class UploadModule {}
