import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Report } from '@/modules/project/entities/report.entity';
import { Project } from '@/modules/project/entities/project.entity';
import { ReportAggregationService, ProjectStatistics } from './report-aggregation.service';

/**
 * Report generation options
 */
export interface GenerateReportOptions {
  includeTopOverpays?: boolean;
  topOverpaysLimit?: number;
  notes?: string;
}

/**
 * ReportService - Report Generation & Versioning
 *
 * Manages report lifecycle:
 * - Generate new reports with versioning
 * - Store data snapshots (immutable)
 * - Track data completeness
 * - List report history
 */
@Injectable()
export class ReportService {
  private readonly logger = new Logger(ReportService.name);

  constructor(
    @InjectRepository(Report)
    private readonly reportRepo: Repository<Report>,
    @InjectRepository(Project)
    private readonly projectRepo: Repository<Project>,
    private readonly aggregationService: ReportAggregationService,
  ) {}

  /**
   * Generate a new report for a project
   * Creates an immutable snapshot of current data
   */
  async generate(
    projectId: string,
    tenantId: string,
    options: GenerateReportOptions = {},
  ): Promise<Report> {
    this.logger.log({
      event: 'generate_report_start',
      project_id: projectId,
    });

    // Verify project exists and belongs to tenant
    const project = await this.projectRepo.findOne({
      where: { id: projectId, tenant_id: tenantId },
    });

    if (!project) {
      throw new NotFoundException(`Project ${projectId} not found`);
    }

    // Calculate next version number
    const latestReport = await this.reportRepo.findOne({
      where: { project_id: projectId },
      order: { version: 'DESC' },
    });

    const nextVersion = latestReport ? latestReport.version + 1 : 1;

    // Aggregate data
    const statistics = await this.aggregationService.calculateProjectStatistics(
      projectId,
      tenantId,
    );

    const dataCompleteness = await this.aggregationService.calculateDataCompleteness(
      projectId,
      tenantId,
    );

    // Calculate date range from shipments
    const dateRangeResult = await this.aggregationService.getDateRange(
      projectId,
      tenantId,
    );

    // Build data snapshot
    const dataSnapshot: Record<string, any> = {
      version: nextVersion,
      generated_at: new Date().toISOString(),
      project: {
        id: project.id,
        name: project.name,
        phase: project.phase,
        status: project.status,
      },
      statistics,
      data_completeness: dataCompleteness,
    };

    // Optionally include top overpays
    if (options.includeTopOverpays) {
      const topOverpays = await this.aggregationService.getTopOverpays(
        projectId,
        tenantId,
        options.topOverpaysLimit || 10,
      );

      dataSnapshot.top_overpays = topOverpays.map((benchmark) => ({
        shipment_id: benchmark.shipment.id,
        date: benchmark.shipment.date,
        carrier: benchmark.shipment.carrier?.name || 'Unknown',
        origin_zip: benchmark.shipment.origin_zip,
        dest_zip: benchmark.shipment.dest_zip,
        actual_cost: benchmark.shipment.actual_total_amount,
        expected_cost: benchmark.expected_total_amount,
        delta: benchmark.delta_amount,
        delta_pct: benchmark.delta_pct,
      }));
    }

    // Create report
    const report = this.reportRepo.create({
      project_id: projectId,
      version: nextVersion,
      report_type: project.phase, // Use project phase as report type
      title: `${project.phase} Report v${nextVersion}`,
      data_snapshot: dataSnapshot,
      data_completeness: dataCompleteness,
      shipment_count: statistics.total_shipments,
      date_range_start: dateRangeResult.start_date || undefined,
      date_range_end: dateRangeResult.end_date || undefined,
      generated_by: tenantId,
      notes: options.notes,
    });

    const savedReport = await this.reportRepo.save(report);

    this.logger.log({
      event: 'generate_report_complete',
      project_id: projectId,
      report_id: savedReport.id,
      version: nextVersion,
      data_completeness: dataCompleteness,
    });

    return savedReport;
  }

  /**
   * Get latest report for a project
   */
  async getLatest(
    projectId: string,
    tenantId: string,
  ): Promise<Report | null> {
    // Verify project belongs to tenant
    const project = await this.projectRepo.findOne({
      where: { id: projectId, tenant_id: tenantId },
    });

    if (!project) {
      throw new NotFoundException(`Project ${projectId} not found`);
    }

    const report = await this.reportRepo.findOne({
      where: { project_id: projectId },
      order: { version: 'DESC' },
    });

    return report;
  }

  /**
   * Get specific report version
   */
  async getByVersion(
    projectId: string,
    tenantId: string,
    version: number,
  ): Promise<Report | null> {
    // Verify project belongs to tenant
    const project = await this.projectRepo.findOne({
      where: { id: projectId, tenant_id: tenantId },
    });

    if (!project) {
      throw new NotFoundException(`Project ${projectId} not found`);
    }

    const report = await this.reportRepo.findOne({
      where: { project_id: projectId, version },
    });

    return report;
  }

  /**
   * List all reports for a project
   */
  async listAll(
    projectId: string,
    tenantId: string,
  ): Promise<Report[]> {
    // Verify project belongs to tenant
    const project = await this.projectRepo.findOne({
      where: { id: projectId, tenant_id: tenantId },
    });

    if (!project) {
      throw new NotFoundException(`Project ${projectId} not found`);
    }

    const reports = await this.reportRepo.find({
      where: { project_id: projectId },
      order: { version: 'DESC' },
    });

    return reports;
  }

  /**
   * Compare two report versions
   */
  async compare(
    projectId: string,
    tenantId: string,
    version1: number,
    version2: number,
  ): Promise<{
    report1: Report;
    report2: Report;
    delta: {
      shipments: number;
      completeness: number;
      savings_potential: number;
    };
  }> {
    const report1 = await this.getByVersion(projectId, tenantId, version1);
    const report2 = await this.getByVersion(projectId, tenantId, version2);

    if (!report1 || !report2) {
      throw new NotFoundException('One or both report versions not found');
    }

    // Calculate deltas
    const stats1 = report1.data_snapshot.statistics as ProjectStatistics;
    const stats2 = report2.data_snapshot.statistics as ProjectStatistics;

    const delta = {
      shipments: stats2.total_shipments - stats1.total_shipments,
      completeness: (report2.data_completeness ?? 0) - (report1.data_completeness ?? 0),
      savings_potential: stats2.total_savings_potential - stats1.total_savings_potential,
    };

    return { report1, report2, delta };
  }

  /**
   * Delete old report versions (keep latest N versions)
   */
  async pruneOldVersions(
    projectId: string,
    tenantId: string,
    keepVersions: number = 5,
  ): Promise<number> {
    // Verify project belongs to tenant
    const project = await this.projectRepo.findOne({
      where: { id: projectId, tenant_id: tenantId },
    });

    if (!project) {
      throw new NotFoundException(`Project ${projectId} not found`);
    }

    // Find versions to delete
    const reports = await this.reportRepo.find({
      where: { project_id: projectId },
      order: { version: 'DESC' },
    });

    if (reports.length <= keepVersions) {
      return 0; // Nothing to prune
    }

    const toDelete = reports.slice(keepVersions);
    const deleteIds = toDelete.map((r) => r.id);

    await this.reportRepo.delete(deleteIds);

    this.logger.log({
      event: 'pruned_old_reports',
      project_id: projectId,
      deleted_count: deleteIds.length,
    });

    return deleteIds.length;
  }
}
