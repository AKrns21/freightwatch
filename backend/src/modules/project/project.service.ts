import { Injectable, NotFoundException, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Project } from './entities/project.entity';
import { ConsultantNote } from './entities/consultant-note.entity';
import { Report } from './entities/report.entity';
import { CreateProjectDto } from './dto/create-project.dto';
import { UpdateProjectDto } from './dto/update-project.dto';
import { CreateNoteDto } from './dto/create-note.dto';
import { Upload } from '../upload/entities/upload.entity';
import { Shipment } from '../parsing/entities/shipment.entity';

/**
 * ProjectService - Manages freight analysis projects
 *
 * Handles project lifecycle from creation through completion,
 * including notes, reports, and status tracking.
 */
@Injectable()
export class ProjectService {
  private readonly logger = new Logger(ProjectService.name);

  constructor(
    @InjectRepository(Project)
    private readonly projectRepo: Repository<Project>,
    @InjectRepository(ConsultantNote)
    private readonly noteRepo: Repository<ConsultantNote>,
    @InjectRepository(Report)
    private readonly reportRepo: Repository<Report>,
    @InjectRepository(Upload)
    private readonly uploadRepo: Repository<Upload>,
    @InjectRepository(Shipment)
    private readonly shipmentRepo: Repository<Shipment>,
  ) {}

  /**
   * Create a new project
   */
  async create(tenantId: string, data: CreateProjectDto): Promise<Project> {
    this.logger.log({
      event: 'project_create',
      tenant_id: tenantId,
      name: data.name
    });

    const project = this.projectRepo.create({
      ...data,
      tenant_id: tenantId,
      phase: data.phase || 'quick_check',
      status: data.status || 'draft',
      metadata: data.metadata || {},
    });

    return this.projectRepo.save(project);
  }

  /**
   * Find all projects for a tenant
   */
  async findAll(tenantId: string): Promise<Project[]> {
    return this.projectRepo.find({
      where: { tenant_id: tenantId, deleted_at: null },
      order: { created_at: 'DESC' },
    });
  }

  /**
   * Find one project by ID
   */
  async findOne(id: string, tenantId: string): Promise<Project> {
    const project = await this.projectRepo.findOne({
      where: { id, tenant_id: tenantId, deleted_at: null },
      relations: ['notes', 'reports'],
    });

    if (!project) {
      throw new NotFoundException(`Project ${id} not found`);
    }

    return project;
  }

  /**
   * Update a project
   */
  async update(
    id: string,
    tenantId: string,
    data: UpdateProjectDto
  ): Promise<Project> {
    const project = await this.findOne(id, tenantId);

    Object.assign(project, data);

    this.logger.log({
      event: 'project_update',
      project_id: id,
      tenant_id: tenantId,
      changes: Object.keys(data)
    });

    return this.projectRepo.save(project);
  }

  /**
   * Soft delete a project
   */
  async remove(id: string, tenantId: string): Promise<void> {
    const project = await this.findOne(id, tenantId);

    this.logger.log({
      event: 'project_delete',
      project_id: id,
      tenant_id: tenantId
    });

    await this.projectRepo.softDelete(id);
  }

  /**
   * Add a consultant note to a project
   */
  async addNote(
    projectId: string,
    tenantId: string,
    note: CreateNoteDto,
    userId: string
  ): Promise<ConsultantNote> {
    // Verify project exists and belongs to tenant
    await this.findOne(projectId, tenantId);

    this.logger.log({
      event: 'note_create',
      project_id: projectId,
      note_type: note.note_type,
      created_by: userId
    });

    const consultantNote = this.noteRepo.create({
      project_id: projectId,
      ...note,
      created_by: userId,
      status: note.status || 'open',
    });

    return this.noteRepo.save(consultantNote);
  }

  /**
   * Get all notes for a project
   */
  async getNotes(projectId: string, tenantId: string): Promise<ConsultantNote[]> {
    // Verify project exists and belongs to tenant
    await this.findOne(projectId, tenantId);

    return this.noteRepo.find({
      where: { project_id: projectId, deleted_at: null },
      order: { created_at: 'DESC' },
    });
  }

  /**
   * Update a consultant note
   */
  async updateNote(
    noteId: string,
    projectId: string,
    tenantId: string,
    updates: Partial<CreateNoteDto>
  ): Promise<ConsultantNote> {
    // Verify project exists
    await this.findOne(projectId, tenantId);

    const note = await this.noteRepo.findOne({
      where: { id: noteId, project_id: projectId, deleted_at: null },
    });

    if (!note) {
      throw new NotFoundException(`Note ${noteId} not found`);
    }

    Object.assign(note, updates);

    this.logger.log({
      event: 'note_update',
      note_id: noteId,
      project_id: projectId
    });

    return this.noteRepo.save(note);
  }

  /**
   * Resolve a consultant note
   */
  async resolveNote(
    noteId: string,
    projectId: string,
    tenantId: string
  ): Promise<ConsultantNote> {
    const note = await this.updateNote(noteId, projectId, tenantId, {
      status: 'resolved' as any,
    });

    note.resolved_at = new Date();
    return this.noteRepo.save(note);
  }

  /**
   * Get all reports for a project
   */
  async getReports(projectId: string, tenantId: string): Promise<Report[]> {
    // Verify project exists
    await this.findOne(projectId, tenantId);

    return this.reportRepo.find({
      where: { project_id: projectId, deleted_at: null },
      order: { version: 'DESC' },
    });
  }

  /**
   * Get project statistics
   */
  async getStatistics(projectId: string, tenantId: string): Promise<any> {
    const project = await this.findOne(projectId, tenantId);
    const notes = await this.getNotes(projectId, tenantId);
    const reports = await this.getReports(projectId, tenantId);

    const openNotes = notes.filter(n => n.status === 'open');
    const criticalNotes = notes.filter(n => n.priority === 'critical');
    const latestReport = reports[0];

    return {
      project_id: projectId,
      name: project.name,
      phase: project.phase,
      status: project.status,
      created_at: project.created_at,
      notes: {
        total: notes.length,
        open: openNotes.length,
        critical: criticalNotes.length,
      },
      reports: {
        total: reports.length,
        latest_version: latestReport?.version || 0,
        latest_completeness: latestReport?.data_completeness || 0,
      },
    };
  }

  /**
   * Get project stats (Phase 5)
   * Enhanced statistics including upload and shipment data
   */
  async getProjectStats(projectId: string, tenantId: string): Promise<any> {
    const project = await this.findOne(projectId, tenantId);

    // Count uploads
    const uploadCount = await this.uploadRepo.count({
      where: { project_id: projectId, tenant_id: tenantId },
    });

    // Count shipments
    const shipmentCount = await this.shipmentRepo.count({
      where: { project_id: projectId, tenant_id: tenantId },
    });

    // Calculate average completeness
    const completenessResult = await this.shipmentRepo
      .createQueryBuilder('s')
      .select('AVG(s.completeness_score)', 'avg_completeness')
      .where('s.project_id = :projectId', { projectId })
      .andWhere('s.tenant_id = :tenantId', { tenantId })
      .getRawOne();

    // Count notes
    const noteCount = await this.noteRepo.count({
      where: { project_id: projectId },
    });

    // Count reports
    const reportCount = await this.reportRepo.count({
      where: { project_id: projectId },
    });

    return {
      project_id: projectId,
      name: project.name,
      upload_count: uploadCount,
      shipment_count: shipmentCount,
      avg_completeness: parseFloat(completenessResult.avg_completeness || '0'),
      note_count: noteCount,
      report_count: reportCount,
      phase: project.phase,
      status: project.status,
      created_at: project.created_at,
    };
  }
}
