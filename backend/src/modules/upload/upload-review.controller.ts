import {
  Controller,
  Get,
  Post,
  Body,
  Param,
  Query,
  UseGuards,
  Req,
  NotFoundException,
} from '@nestjs/common';
import { UploadService } from './upload.service';
import { TemplateService } from '@/modules/parsing/template.service';
import { JwtAuthGuard } from '@/modules/auth/guards/jwt-auth.guard';

/**
 * UploadReviewController - Review and correction workflow
 *
 * Provides endpoints for consultant review of parsed uploads:
 * - View parsing results and LLM analysis
 * - Preview file content
 * - Accept/reject suggested mappings
 * - Save corrections as templates
 * - Mark uploads as reviewed
 */
@Controller('uploads/:uploadId/review')
@UseGuards(JwtAuthGuard)
export class UploadReviewController {
  constructor(
    private readonly uploadService: UploadService,
    private readonly templateService: TemplateService,
  ) {}

  /**
   * Get review data for an upload
   * GET /uploads/:uploadId/review
   */
  @Get()
  async getReviewData(
    @Param('uploadId') uploadId: string,
    @Query('previewLines') previewLines: number = 50,
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    // Load upload
    const upload = await this.uploadService.findById(
      uploadId,
      tenantId,
    );

    if (!upload) {
      throw new NotFoundException(`Upload ${uploadId} not found`);
    }

    // Get file preview
    const preview = await this.uploadService.getPreview(
      uploadId,
      tenantId,
      previewLines,
    );

    // Get shipments for this upload
    const shipments = await this.uploadService.getShipments(
      uploadId,
      tenantId,
    );

    return {
      success: true,
      data: {
        upload: {
          id: upload.id,
          filename: upload.filename,
          status: upload.status,
          parse_method: upload.parse_method,
          confidence: upload.confidence,
          received_at: upload.received_at,
          project_id: upload.project_id,
        },
        llm_analysis: upload.llm_analysis,
        suggested_mappings: upload.suggested_mappings,
        parsing_issues: upload.parsing_issues,
        preview,
        shipments: {
          total: shipments.length,
          parsed: shipments.filter((s) => !s.deleted_at).length,
          completeness_avg:
            shipments.length > 0
              ? shipments.reduce(
                  (sum, s) => sum + (s.completeness_score || 0),
                  0,
                ) / shipments.length
              : 0,
        },
      },
    };
  }

  /**
   * Get file preview (first N lines)
   * GET /uploads/:uploadId/review/preview?lines=100
   */
  @Get('preview')
  async getPreview(
    @Param('uploadId') uploadId: string,
    @Query('lines') lines: number = 50,
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    const preview = await this.uploadService.getPreview(
      uploadId,
      tenantId,
      lines,
    );

    return {
      success: true,
      data: preview,
    };
  }

  /**
   * Accept suggested mappings and re-parse
   * POST /uploads/:uploadId/review/accept-mappings
   */
  @Post('accept-mappings')
  async acceptMappings(
    @Param('uploadId') uploadId: string,
    @Body()
    body: {
      mappings: Record<string, string>;
      save_as_template?: boolean;
      template_name?: string;
    },
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    // Apply mappings and re-parse
    await this.uploadService.applyMappings(
      uploadId,
      tenantId,
      body.mappings,
    );

    // Optionally save as template
    if (body.save_as_template) {
      await this.templateService.createFromUpload(
        uploadId,
        tenantId,
        {
          name: body.template_name || `Template for ${uploadId}`,
          mappings: body.mappings,
        },
      );
    }

    return {
      success: true,
      message: 'Mappings applied and upload re-parsed',
    };
  }

  /**
   * Reject mappings and provide corrections
   * POST /uploads/:uploadId/review/reject-mappings
   */
  @Post('reject-mappings')
  async rejectMappings(
    @Param('uploadId') uploadId: string,
    @Body()
    body: {
      corrected_mappings: Record<string, string>;
      notes?: string;
      save_as_template?: boolean;
      template_name?: string;
    },
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    // Apply corrected mappings
    await this.uploadService.applyMappings(
      uploadId,
      tenantId,
      body.corrected_mappings,
    );

    // Save as template with consultant's corrections
    if (body.save_as_template) {
      await this.templateService.createFromUpload(
        uploadId,
        tenantId,
        {
          name:
            body.template_name ||
            `Corrected template for ${uploadId}`,
          mappings: body.corrected_mappings,
          notes: body.notes,
        },
      );
    }

    return {
      success: true,
      message: 'Corrected mappings applied',
    };
  }

  /**
   * Mark upload as reviewed (quality check complete)
   * POST /uploads/:uploadId/review/approve
   */
  @Post('approve')
  async approve(
    @Param('uploadId') uploadId: string,
    @Body() body: { notes?: string },
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;
    const userId = req.user.id;

    await this.uploadService.markAsReviewed(
      uploadId,
      tenantId,
      userId,
      body.notes,
    );

    return {
      success: true,
      message: 'Upload approved',
    };
  }

  /**
   * Mark upload for re-processing
   * POST /uploads/:uploadId/review/reprocess
   */
  @Post('reprocess')
  async reprocess(
    @Param('uploadId') uploadId: string,
    @Body()
    body: {
      reason?: string;
      force_llm?: boolean;
    },
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    await this.uploadService.reprocess(uploadId, tenantId, {
      reason: body.reason,
      force_llm: body.force_llm,
    });

    return {
      success: true,
      message: 'Upload queued for re-processing',
    };
  }

  /**
   * Get parsing issues for upload
   * GET /uploads/:uploadId/review/issues
   */
  @Get('issues')
  async getIssues(
    @Param('uploadId') uploadId: string,
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    const upload = await this.uploadService.findById(
      uploadId,
      tenantId,
    );

    if (!upload) {
      throw new NotFoundException(`Upload ${uploadId} not found`);
    }

    return {
      success: true,
      data: {
        issues: upload.parsing_issues || [],
        confidence: upload.confidence,
        parse_method: upload.parse_method,
      },
    };
  }

  /**
   * Get data quality metrics for upload
   * GET /uploads/:uploadId/review/quality
   */
  @Get('quality')
  async getQuality(
    @Param('uploadId') uploadId: string,
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    const quality = await this.uploadService.getQualityMetrics(
      uploadId,
      tenantId,
    );

    return {
      success: true,
      data: quality,
    };
  }
}
