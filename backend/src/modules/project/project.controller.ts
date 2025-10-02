import {
  Controller,
  Get,
  Post,
  Put,
  Delete,
  Body,
  Param,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { ProjectService } from './project.service';
import { CreateProjectDto } from './dto/create-project.dto';
import { UpdateProjectDto } from './dto/update-project.dto';
import { CreateNoteDto } from './dto/create-note.dto';
import { TenantId, UserId } from '@/modules/auth/tenant.decorator';

/**
 * ProjectController - HTTP endpoints for project management
 *
 * Provides REST API for freight analysis project operations.
 * All endpoints require authentication and tenant context.
 */
@Controller('projects')
export class ProjectController {
  constructor(private readonly projectService: ProjectService) {}

  /**
   * Create a new project
   * POST /api/projects
   */
  @Post()
  @HttpCode(HttpStatus.CREATED)
  async create(
    @Body() createProjectDto: CreateProjectDto,
    @TenantId() tenantId: string,
  ) {
    const project = await this.projectService.create(tenantId, createProjectDto);
    return {
      success: true,
      data: project.toSafeObject(),
    };
  }

  /**
   * Get all projects for current tenant
   * GET /api/projects
   */
  @Get()
  async findAll(@TenantId() tenantId: string) {
    const projects = await this.projectService.findAll(tenantId);
    return {
      success: true,
      data: projects.map(p => p.toSafeObject()),
    };
  }

  /**
   * Get one project by ID
   * GET /api/projects/:id
   */
  @Get(':id')
  async findOne(@Param('id') id: string, @TenantId() tenantId: string) {
    const project = await this.projectService.findOne(id, tenantId);
    return {
      success: true,
      data: project.toSafeObject(),
    };
  }

  /**
   * Update a project
   * PUT /api/projects/:id
   */
  @Put(':id')
  async update(
    @Param('id') id: string,
    @Body() updateProjectDto: UpdateProjectDto,
    @TenantId() tenantId: string,
  ) {
    const project = await this.projectService.update(id, tenantId, updateProjectDto);
    return {
      success: true,
      data: project.toSafeObject(),
    };
  }

  /**
   * Delete a project
   * DELETE /api/projects/:id
   */
  @Delete(':id')
  @HttpCode(HttpStatus.NO_CONTENT)
  async remove(@Param('id') id: string, @TenantId() tenantId: string) {
    await this.projectService.remove(id, tenantId);
  }

  /**
   * Get project statistics (legacy endpoint)
   * GET /api/projects/:id/statistics
   */
  @Get(':id/statistics')
  async getStatistics(@Param('id') id: string, @TenantId() tenantId: string) {
    const stats = await this.projectService.getStatistics(id, tenantId);
    return {
      success: true,
      data: stats,
    };
  }

  /**
   * Get project stats (Phase 5)
   * GET /api/projects/:id/stats
   * Enhanced statistics with upload and shipment counts
   */
  @Get(':id/stats')
  async getStats(@Param('id') id: string, @TenantId() tenantId: string) {
    const stats = await this.projectService.getProjectStats(id, tenantId);
    return {
      success: true,
      data: stats,
    };
  }

  /**
   * Add a note to a project
   * POST /api/projects/:id/notes
   */
  @Post(':id/notes')
  @HttpCode(HttpStatus.CREATED)
  async addNote(
    @Param('id') projectId: string,
    @Body() createNoteDto: CreateNoteDto,
    @TenantId() tenantId: string,
    @UserId() userId: string,
  ) {
    const note = await this.projectService.addNote(
      projectId,
      tenantId,
      createNoteDto,
      userId
    );
    return {
      success: true,
      data: note.toSafeObject(),
    };
  }

  /**
   * Get all notes for a project
   * GET /api/projects/:id/notes
   */
  @Get(':id/notes')
  async getNotes(@Param('id') projectId: string, @TenantId() tenantId: string) {
    const notes = await this.projectService.getNotes(projectId, tenantId);
    return {
      success: true,
      data: notes.map(n => n.toSafeObject()),
    };
  }

  /**
   * Update a note
   * PUT /api/projects/:id/notes/:noteId
   */
  @Put(':id/notes/:noteId')
  async updateNote(
    @Param('id') projectId: string,
    @Param('noteId') noteId: string,
    @Body() updates: Partial<CreateNoteDto>,
    @TenantId() tenantId: string,
  ) {
    const note = await this.projectService.updateNote(
      noteId,
      projectId,
      tenantId,
      updates
    );
    return {
      success: true,
      data: note.toSafeObject(),
    };
  }

  /**
   * Resolve a note
   * POST /api/projects/:id/notes/:noteId/resolve
   */
  @Post(':id/notes/:noteId/resolve')
  async resolveNote(
    @Param('id') projectId: string,
    @Param('noteId') noteId: string,
    @TenantId() tenantId: string,
  ) {
    const note = await this.projectService.resolveNote(noteId, projectId, tenantId);
    return {
      success: true,
      data: note.toSafeObject(),
    };
  }

  /**
   * Get all reports for a project
   * GET /api/projects/:id/reports
   */
  @Get(':id/reports')
  async getReports(@Param('id') projectId: string, @TenantId() tenantId: string) {
    const reports = await this.projectService.getReports(projectId, tenantId);
    return {
      success: true,
      data: reports.map(r => r.toSafeObject()),
    };
  }
}
