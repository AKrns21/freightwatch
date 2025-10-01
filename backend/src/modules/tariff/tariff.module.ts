import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { TariffZoneMap } from './entities/tariff-zone-map.entity';
import { FxRate } from './entities/fx-rate.entity';
import { TariffTable } from './entities/tariff-table.entity';
import { TariffRate } from './entities/tariff-rate.entity';
import { ZoneCalculatorService } from './zone-calculator.service';
import { FxService } from './fx.service';
import { TariffEngineService } from './tariff-engine.service';

@Module({
  imports: [TypeOrmModule.forFeature([
    TariffZoneMap, 
    FxRate, 
    TariffTable, 
    TariffRate
  ])],
  providers: [
    ZoneCalculatorService, 
    FxService, 
    TariffEngineService
  ],
  exports: [
    ZoneCalculatorService, 
    FxService, 
    TariffEngineService
  ],
})
export class TariffModule {}