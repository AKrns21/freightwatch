import {
  Controller,
  Post,
  UseInterceptors,
  UploadedFile,
  BadRequestException,
  Body,
  Get,
  HttpStatus,
  HttpCode,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { TenantId } from '@/modules/auth/tenant.decorator';
import { UploadService } from './upload.service';

const ALLOWED_MIME_TYPES = [
  'text/csv',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/pdf',
];

const ALLOWED_EXTENSIONS = ['.csv', '.xls', '.xlsx', '.pdf'];
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

class UploadFileDto {
  sourceType!: string;
}

@Controller('upload')
export class UploadController {
  constructor(private readonly uploadService: UploadService) {}

  @Post()
  @HttpCode(HttpStatus.OK)
  @UseInterceptors(
    FileInterceptor('file', {
      limits: {
        fileSize: MAX_FILE_SIZE,
      },
      fileFilter: (_req, file, callback) => {
        if (!ALLOWED_MIME_TYPES.includes(file.mimetype)) {
          const fileName = file.originalname;
          const extension = fileName.substring(fileName.lastIndexOf('.')).toLowerCase();

          if (!ALLOWED_EXTENSIONS.includes(extension)) {
            return callback(
              new BadRequestException(
                `File type not allowed. Allowed types: ${ALLOWED_EXTENSIONS.join(', ')}`
              ),
              false
            );
          }
        }

        callback(null, true);
      },
    })
  )
  async uploadFile(
    @UploadedFile() file: Express.Multer.File,
    @Body() uploadFileDto: UploadFileDto,
    @TenantId() tenantId: string
  ): Promise<{
    success: boolean;
    upload: unknown;
    alreadyProcessed: boolean;
    message: string;
  }> {
    if (!file) {
      throw new BadRequestException('No file provided');
    }

    if (!uploadFileDto.sourceType) {
      throw new BadRequestException('sourceType is required');
    }

    const validSourceTypes = ['invoice', 'rate_card', 'fleet_log'];
    if (!validSourceTypes.includes(uploadFileDto.sourceType)) {
      throw new BadRequestException(
        `Invalid sourceType. Allowed values: ${validSourceTypes.join(', ')}`
      );
    }

    if (file.size > MAX_FILE_SIZE) {
      throw new BadRequestException(
        `File size exceeds maximum allowed size of ${MAX_FILE_SIZE / 1024 / 1024}MB`
      );
    }

    const result = await this.uploadService.uploadFile(file, tenantId, uploadFileDto.sourceType);

    return {
      success: true,
      upload: result.upload.toSafeObject(),
      alreadyProcessed: result.alreadyProcessed,
      message: result.alreadyProcessed
        ? 'File already exists and was previously processed'
        : 'File uploaded successfully',
    };
  }

  @Get()
  async getUploads(
    @TenantId() tenantId: string
  ): Promise<{ success: boolean; uploads: unknown[] }> {
    const uploads = await this.uploadService.findByTenant(tenantId);

    return {
      success: true,
      uploads: uploads.map((upload) => upload.toSafeObject()),
    };
  }
}
