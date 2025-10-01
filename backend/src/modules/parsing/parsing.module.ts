import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './entities/shipment.entity';
import { ServiceCatalog } from './entities/service-catalog.entity';
import { ServiceAlias } from './entities/service-alias.entity';
import { CsvParserService } from './csv-parser.service';
import { ServiceMapperService } from './service-mapper.service';

@Module({
  imports: [TypeOrmModule.forFeature([Shipment, ServiceCatalog, ServiceAlias])],
  providers: [CsvParserService, ServiceMapperService],
  exports: [CsvParserService, ServiceMapperService],
})
export class ParsingModule {}