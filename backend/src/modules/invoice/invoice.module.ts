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
import { ParsingModule } from '@/modules/parsing/parsing.module';

/**
 * InvoiceModule - Invoice Parsing & Matching
 *
 * Provides services for processing carrier invoices:
 * - Parse invoice PDFs (header + line items)
 * - Match invoice lines to shipments
 * - Track matching statistics
 * - Support manual matching/corrections
 */
@Module({
  imports: [
    TypeOrmModule.forFeature([InvoiceHeader, InvoiceLine, InvoiceDisputeEvent, ParsingTemplate, Shipment, Upload]),
    ParsingModule,
  ],
  providers: [InvoiceParserService, InvoiceMatcherService, PdfVisionService],
  controllers: [InvoiceController],
  exports: [InvoiceParserService, InvoiceMatcherService],
})
export class InvoiceModule {}
