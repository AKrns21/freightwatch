import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './entities/shipment.entity';
import { CsvParserService } from './csv-parser.service';

@Module({
  imports: [TypeOrmModule.forFeature([Shipment])],
  providers: [CsvParserService],
  exports: [CsvParserService],
})
export class ParsingModule {}