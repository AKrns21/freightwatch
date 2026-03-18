import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { InvoiceHeader } from './entities/invoice-header.entity';
import { InvoiceLine } from './entities/invoice-line.entity';
import { InvoiceDisputeEvent } from './entities/invoice-dispute-event.entity';
import { InvoiceParserService } from './invoice-parser.service';
import { InvoiceMatcherService } from './invoice-matcher.service';
import { PdfVisionService } from './pdf-vision.service';
import { InvoiceController } from './invoice.controller';
import { ParsingTemplate } from '@/modules/parsing/entities/parsing-template.entity';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';
import { Upload } from '@/modules/upload/entities/upload.entity';
import { RawExtraction } from '@/modules/upload/entities/raw-extraction.entity';
import { ParsingModule } from '@/modules/parsing/parsing.module';

// Vision pipeline — 6 stage services (Issue #23)
import { PreProcessorService } from './vision-pipeline/pre-processor.service';
import { PageClassifierService } from './vision-pipeline/page-classifier.service';
import { StructuredExtractorService } from './vision-pipeline/structured-extractor.service';
import { CrossDocumentValidatorService } from './vision-pipeline/cross-document-validator.service';
import { ConfidenceScorerService } from './vision-pipeline/confidence-scorer.service';
import { ReviewGateService } from './vision-pipeline/review-gate.service';
import { VisionPipelineService } from './vision-pipeline/vision-pipeline.service';

/**
 * InvoiceModule - Invoice Parsing & Matching
 *
 * Provides services for processing carrier invoices:
 * - Parse invoice PDFs (header + line items) via 6-stage vision pipeline
 * - Match invoice lines to shipments
 * - Track matching statistics
 * - Support manual matching/corrections
 */
@Module({
  imports: [
    TypeOrmModule.forFeature([
      InvoiceHeader,
      InvoiceLine,
      InvoiceDisputeEvent,
      ParsingTemplate,
      Shipment,
      Upload,
      RawExtraction,
    ]),
    ParsingModule,
  ],
  providers: [
    InvoiceParserService,
    InvoiceMatcherService,
    PdfVisionService,
    // Vision pipeline stages
    PreProcessorService,
    PageClassifierService,
    StructuredExtractorService,
    CrossDocumentValidatorService,
    ConfidenceScorerService,
    ReviewGateService,
    VisionPipelineService,
  ],
  controllers: [InvoiceController],
  exports: [InvoiceParserService, InvoiceMatcherService],
})
export class InvoiceModule {}
