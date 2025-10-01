import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Report } from '../project/entities/report.entity';
import { Project } from '../project/entities/project.entity';
import { Shipment } from '../parsing/entities/shipment.entity';
import { ShipmentBenchmark } from '../tariff/entities/shipment-benchmark.entity';
import { ReportService } from './report.service';
import { ReportAggregationService } from './report-aggregation.service';
import { ReportController } from './report.controller';

/**
 * ReportModule - Report Generation & Aggregation
 *
 * Generates versioned reports with aggregated data snapshots:
 * - Project-level cost analysis
 * - Carrier comparisons
 * - Overpay detection summaries
 * - Data completeness tracking
 */
@Module({
  imports: [
    TypeOrmModule.forFeature([
      Report,
      Project,
      Shipment,
      ShipmentBenchmark,
    ]),
  ],
  providers: [ReportService, ReportAggregationService],
  controllers: [ReportController],
  exports: [ReportService, ReportAggregationService],
})
export class ReportModule {}
