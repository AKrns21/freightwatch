import {
  Controller,
  Get,
  Post,
  Query,
  Param,
  Body,
  UseGuards,
  Req,
  NotFoundException,
} from '@nestjs/common';
import { ReportService, GenerateReportOptions } from './report.service';
import { ReportAggregationService } from './report-aggregation.service';
import { JwtAuthGuard } from '@/modules/auth/guards/jwt-auth.guard';
import { TenantRequest } from '@/modules/auth/tenant.interceptor';

/**
 * ReportController - Report API Endpoints
 *
 * Endpoints:
 * - POST /reports/generate - Generate new report
 * - GET /reports/latest - Get latest report
 * - GET /reports/version/:version - Get specific version
 * - GET /reports/list - List all reports for project
 * - GET /reports/compare - Compare two versions
 * - GET /reports/statistics - Get live statistics (not saved)
 * - POST /reports/prune - Delete old versions
 */
@Controller('reports')
@UseGuards(JwtAuthGuard)
export class ReportController {
  constructor(
    private readonly reportService: ReportService,
    private readonly aggregationService: ReportAggregationService
  ) {}

  /**
   * Generate a new report for a project
   * POST /reports/generate?projectId=xxx
   */
  @Post('generate')
  async generate(
    @Query('projectId') projectId: string,
    @Body() options: GenerateReportOptions,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: unknown }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const report = await this.reportService.generate(projectId, tenantId, options);

    return {
      success: true,
      data: report,
    };
  }

  /**
   * Get latest report for a project
   * GET /reports/latest?projectId=xxx
   */
  @Get('latest')
  async getLatest(
    @Query('projectId') projectId: string,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: unknown }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const report = await this.reportService.getLatest(projectId, tenantId);

    if (!report) {
      throw new NotFoundException('No reports found for this project');
    }

    return {
      success: true,
      data: report,
    };
  }

  /**
   * Get specific report version
   * GET /reports/version/:version?projectId=xxx
   */
  @Get('version/:version')
  async getByVersion(
    @Param('version') version: number,
    @Query('projectId') projectId: string,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: unknown }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const report = await this.reportService.getByVersion(projectId, tenantId, version);

    if (!report) {
      throw new NotFoundException(`Report version ${version} not found`);
    }

    return {
      success: true,
      data: report,
    };
  }

  /**
   * List all reports for a project
   * GET /reports/list?projectId=xxx
   */
  @Get('list')
  async listAll(
    @Query('projectId') projectId: string,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: unknown[] }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const reports = await this.reportService.listAll(projectId, tenantId);

    return {
      success: true,
      data: reports,
    };
  }

  /**
   * Compare two report versions
   * GET /reports/compare?projectId=xxx&v1=1&v2=2
   */
  @Get('compare')
  async compare(
    @Query('projectId') projectId: string,
    @Query('v1') version1: number,
    @Query('v2') version2: number,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: unknown }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const comparison = await this.reportService.compare(projectId, tenantId, version1, version2);

    return {
      success: true,
      data: comparison,
    };
  }

  /**
   * Get live statistics (not saved as report)
   * GET /reports/statistics?projectId=xxx
   */
  @Get('statistics')
  async getStatistics(
    @Query('projectId') projectId: string,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: unknown }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const statistics = await this.aggregationService.calculateProjectStatistics(
      projectId,
      tenantId
    );

    return {
      success: true,
      data: statistics,
    };
  }

  /**
   * Get top overpay shipments
   * GET /reports/top-overpays?projectId=xxx&limit=10
   */
  @Get('top-overpays')
  async getTopOverpays(
    @Query('projectId') projectId: string,
    @Query('limit') limit: number = 10,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: unknown[] }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const overpays = await this.aggregationService.getTopOverpays(projectId, tenantId, limit);

    return {
      success: true,
      data: overpays,
    };
  }

  /**
   * Prune old report versions
   * POST /reports/prune?projectId=xxx&keepVersions=5
   */
  @Post('prune')
  async pruneOldVersions(
    @Query('projectId') projectId: string,
    @Query('keepVersions') keepVersions: number = 5,
    @Req() req: TenantRequest
  ): Promise<{ success: boolean; data: { deleted_count: number } }> {
    const tenantId = req.user?.tenantId || req.tenantId;

    const deletedCount = await this.reportService.pruneOldVersions(
      projectId,
      tenantId,
      keepVersions
    );

    return {
      success: true,
      data: {
        deleted_count: deletedCount,
      },
    };
  }
}
