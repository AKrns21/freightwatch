import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { TariffZoneMap } from './entities/tariff-zone-map.entity';
import { FxRate } from './entities/fx-rate.entity';
import { ZoneCalculatorService } from './zone-calculator.service';
import { FxService } from './fx.service';

@Module({
  imports: [TypeOrmModule.forFeature([TariffZoneMap, FxRate])],
  providers: [ZoneCalculatorService, FxService],
  exports: [ZoneCalculatorService, FxService],
})
export class TariffModule {}