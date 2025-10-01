import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { TariffZoneMap } from './entities/tariff-zone-map.entity';
import { ZoneCalculatorService } from './zone-calculator.service';

@Module({
  imports: [TypeOrmModule.forFeature([TariffZoneMap])],
  providers: [ZoneCalculatorService],
  exports: [ZoneCalculatorService],
})
export class TariffModule {}