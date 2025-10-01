import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { Upload } from './entities/upload.entity';
import { UploadService } from './upload.service';
import { UploadController } from './upload.controller';

@Module({
  imports: [TypeOrmModule.forFeature([Upload])],
  controllers: [UploadController],
  providers: [UploadService],
  exports: [UploadService],
})
export class UploadModule {}