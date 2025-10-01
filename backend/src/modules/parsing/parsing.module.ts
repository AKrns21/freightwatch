import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Shipment } from './entities/shipment.entity';
import { ServiceCatalog } from './entities/service-catalog.entity';
import { ServiceAlias } from './entities/service-alias.entity';
import { ParsingTemplate } from './entities/parsing-template.entity';
import { ManualMapping } from './entities/manual-mapping.entity';
import { Upload } from '../upload/entities/upload.entity';
import { CsvParserService } from './csv-parser.service';
import { ServiceMapperService } from './service-mapper.service';
import { LlmParserService } from './services/llm-parser.service';
import { TemplateMatcherService } from './services/template-matcher.service';
import { TemplateService } from './template.service';

/**
 * ParsingModule - File parsing and analysis
 *
 * Provides services for parsing various file formats with hybrid approach:
 * - Template-based parsing (fast, deterministic)
 * - LLM-based analysis (flexible, learns from corrections)
 */
@Module({
  imports: [
    TypeOrmModule.forFeature([
      Shipment,
      ServiceCatalog,
      ServiceAlias,
      ParsingTemplate,
      ManualMapping,
      Upload,
    ]),
  ],
  providers: [
    CsvParserService,
    ServiceMapperService,
    LlmParserService,
    TemplateMatcherService,
    TemplateService,
  ],
  exports: [
    CsvParserService,
    ServiceMapperService,
    LlmParserService,
    TemplateMatcherService,
    TemplateService,
  ],
})
export class ParsingModule {}