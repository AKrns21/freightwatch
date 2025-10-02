import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { BullModule } from '@nestjs/bull';
import { Upload } from './entities/upload.entity';
import { CarrierAlias } from './entities/carrier-alias.entity';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';
import { UploadService } from './upload.service';
import { UploadController } from './upload.controller';
import { UploadReviewController } from './upload-review.controller';
import { UploadProcessor } from './upload-processor.service';
import { ParsingModule } from '@/modules/parsing/parsing.module';
import { TariffModule } from '@/modules/tariff/tariff.module';

@Module({
  imports: [
    TypeOrmModule.forFeature([Upload, CarrierAlias, Shipment]),
    BullModule.registerQueue({
      name: 'upload',
    }),
    ParsingModule,
    TariffModule,
  ],
  controllers: [UploadController, UploadReviewController],
  providers: [UploadService, UploadProcessor],
  exports: [UploadService],
})
export class UploadModule {}