import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Project } from './entities/project.entity';
import { ConsultantNote } from './entities/consultant-note.entity';
import { Report } from './entities/report.entity';
import { Upload } from '@/modules/upload/entities/upload.entity';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';
import { ProjectService } from './project.service';
import { ProjectController } from './project.controller';

/**
 * ProjectModule - Freight analysis project management
 *
 * Provides project workspace functionality for consultants to manage
 * freight cost analysis from upload through report generation.
 */
@Module({
  imports: [
    TypeOrmModule.forFeature([
      Project,
      ConsultantNote,
      Report,
      Upload,
      Shipment,
    ]),
  ],
  controllers: [ProjectController],
  providers: [ProjectService],
  exports: [ProjectService],
})
export class ProjectModule {}
