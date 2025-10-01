import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { TariffZoneMap } from './entities/tariff-zone-map.entity';
import { FxRate } from './entities/fx-rate.entity';
import { TariffTable } from './entities/tariff-table.entity';
import { TariffRate } from './entities/tariff-rate.entity';
import { DieselFloater } from './entities/diesel-floater.entity';
import { ShipmentBenchmark } from './entities/shipment-benchmark.entity';
import { Carrier } from '../upload/entities/carrier.entity';
import { ZoneCalculatorService } from './zone-calculator.service';
import { FxService } from './fx.service';
import { TariffEngineService } from './tariff-engine.service';
import { TariffPdfParserService } from './tariff-pdf-parser.service';
import { ParsingTemplate } from '../parsing/entities/parsing-template.entity';
import { ParsingModule } from '../parsing/parsing.module';

/**
 * TariffModule - Phase 2 Refactored
 *
 * Changes:
 * - Removed TariffRule entity (table dropped in migration 003)
 * - Added Carrier entity (for conversion_rules access)
 */
@Module({
  imports: [
    TypeOrmModule.forFeature([
      TariffZoneMap,
      FxRate,
      TariffTable,
      TariffRate,
      DieselFloater,
      ShipmentBenchmark,
      Carrier,
      ParsingTemplate,
    ]),
    ParsingModule,
  ],
  providers: [
    ZoneCalculatorService,
    FxService,
    TariffEngineService,
    TariffPdfParserService,
  ],
  exports: [
    ZoneCalculatorService,
    FxService,
    TariffEngineService,
    TariffPdfParserService,
  ],
})
export class TariffModule {}