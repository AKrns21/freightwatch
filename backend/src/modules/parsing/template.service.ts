import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { ParsingTemplate } from './entities/parsing-template.entity';
import { Upload } from '../upload/entities/upload.entity';

/**
 * Template creation options
 */
export interface CreateTemplateOptions {
  name: string;
  mappings: Record<string, string>;
  notes?: string;
  detection_rules?: {
    filename_pattern?: string;
    header_keywords?: string[];
    mime_types?: string[];
  };
}

/**
 * TemplateService - Manage parsing templates
 *
 * Provides functionality for:
 * - Creating templates from uploads
 * - Saving consultant corrections as templates
 * - Managing template lifecycle
 * - Template versioning
 */
@Injectable()
export class TemplateService {
  private readonly logger = new Logger(TemplateService.name);

  constructor(
    @InjectRepository(ParsingTemplate)
    private readonly templateRepo: Repository<ParsingTemplate>,
    @InjectRepository(Upload)
    private readonly uploadRepo: Repository<Upload>,
  ) {}

  /**
   * Create template from mappings (Phase 4)
   * Alias for createFromUpload with swapped parameter order
   */
  async createFromMappings(
    tenantId: string,
    uploadId: string,
    mappings: any[],
    templateName: string,
  ): Promise<ParsingTemplate> {
    // Convert array mappings to Record format
    const mappingsRecord: Record<string, string> = {};
    for (const mapping of mappings) {
      if (mapping.field && mapping.column) {
        mappingsRecord[mapping.field] = mapping.column;
      }
    }

    return this.createFromUpload(uploadId, tenantId, {
      name: templateName,
      mappings: mappingsRecord,
    });
  }

  /**
   * Create template from an upload
   */
  async createFromUpload(
    uploadId: string,
    tenantId: string,
    options: CreateTemplateOptions,
  ): Promise<ParsingTemplate> {
    this.logger.log({
      event: 'create_template_from_upload',
      upload_id: uploadId,
      template_name: options.name,
    });

    // Load upload
    const upload = await this.uploadRepo.findOne({
      where: { id: uploadId, tenant_id: tenantId },
    });

    if (!upload) {
      throw new Error(`Upload ${uploadId} not found`);
    }

    // Extract detection rules from upload
    const detection = options.detection_rules || {
      filename_pattern: this.extractFilenamePattern(upload.filename),
      header_keywords: this.extractHeaderKeywords(
        options.mappings,
      ),
      mime_types: [upload.mime_type],
    };

    // Determine template category
    const category = this.detectCategory(upload, options.mappings);

    // Create template
    const template = this.templateRepo.create({
      tenant_id: tenantId,
      name: options.name,
      template_category: category,
      detection,
      mappings: options.mappings,
      notes: options.notes,
      created_by: tenantId, // TODO: Get actual user ID
      usage_count: 0,
    });

    const saved = await this.templateRepo.save(template);

    this.logger.log({
      event: 'template_created',
      template_id: saved.id,
      template_name: saved.name,
      category,
    });

    return saved;
  }

  /**
   * Update existing template
   */
  async update(
    templateId: string,
    tenantId: string,
    updates: Partial<CreateTemplateOptions>,
  ): Promise<ParsingTemplate> {
    const template = await this.templateRepo.findOne({
      where: { id: templateId, tenant_id: tenantId },
    });

    if (!template) {
      throw new Error(`Template ${templateId} not found`);
    }

    // Update fields
    if (updates.name) {
      template.name = updates.name;
    }

    if (updates.mappings) {
      template.mappings = updates.mappings;
    }

    if (updates.notes !== undefined) {
      template.notes = updates.notes;
    }

    if (updates.detection_rules) {
      template.detection = {
        ...template.detection,
        ...updates.detection_rules,
      };
    }

    return this.templateRepo.save(template);
  }

  /**
   * Delete template (soft delete)
   */
  async delete(templateId: string, tenantId: string): Promise<void> {
    await this.templateRepo.softDelete({
      id: templateId,
      tenant_id: tenantId,
    });

    this.logger.log({
      event: 'template_deleted',
      template_id: templateId,
    });
  }

  /**
   * Get all templates for tenant
   */
  async findAll(tenantId: string): Promise<ParsingTemplate[]> {
    return this.templateRepo.find({
      where: [
        { tenant_id: tenantId, deleted_at: null },
        { tenant_id: null, deleted_at: null }, // Global templates
      ],
      order: { usage_count: 'DESC', created_at: 'DESC' },
    });
  }

  /**
   * Get templates by category
   */
  async findByCategory(
    tenantId: string,
    category: string,
  ): Promise<ParsingTemplate[]> {
    return this.templateRepo.find({
      where: [
        {
          tenant_id: tenantId,
          template_category: category,
          deleted_at: null,
        },
        {
          tenant_id: null,
          template_category: category,
          deleted_at: null,
        },
      ],
      order: { usage_count: 'DESC' },
    });
  }

  /**
   * Increment template usage counter
   */
  async incrementUsage(templateId: string): Promise<void> {
    await this.templateRepo.increment(
      { id: templateId },
      'usage_count',
      1,
    );

    await this.templateRepo.update(templateId, {
      last_used_at: new Date(),
    });
  }

  /**
   * Extract filename pattern from specific filename
   */
  private extractFilenamePattern(filename: string): string {
    // Remove date patterns (YYYY-MM-DD, YYYYMMDD, etc.)
    let pattern = filename
      .replace(/\d{4}-\d{2}-\d{2}/g, '\\d{4}-\\d{2}-\\d{2}')
      .replace(/\d{8}/g, '\\d{8}')
      .replace(/\d{6}/g, '\\d{6}');

    // Remove common variable parts
    pattern = pattern
      .replace(/\d+/g, '\\d+') // Any number
      .replace(/\s+/g, '\\s*'); // Whitespace

    return pattern;
  }

  /**
   * Extract header keywords from mappings
   */
  private extractHeaderKeywords(
    mappings: Record<string, string>,
  ): string[] {
    // Use the source column names as keywords
    const keywords = Object.keys(mappings).map((key) =>
      key.toLowerCase().trim(),
    );

    return [...new Set(keywords)]; // Deduplicate
  }

  /**
   * Detect template category from upload and mappings
   */
  private detectCategory(
    upload: Upload,
    mappings: Record<string, string>,
  ): string {
    // Check for common field patterns
    const fields = Object.values(mappings).map((f) => f.toLowerCase());

    // Invoice detection
    if (
      fields.some((f) => f.includes('invoice')) ||
      fields.some((f) => f.includes('line'))
    ) {
      return 'invoice';
    }

    // Tariff detection
    if (
      fields.some((f) => f.includes('zone')) ||
      fields.some((f) => f.includes('weight_band')) ||
      fields.some((f) => f.includes('price'))
    ) {
      return 'tariff';
    }

    // Shipment list detection
    if (
      fields.some((f) => f.includes('origin')) &&
      fields.some((f) => f.includes('dest')) &&
      fields.some((f) => f.includes('weight'))
    ) {
      return 'shipment_list';
    }

    // Default
    return 'unknown';
  }

  /**
   * Clone template with modifications
   */
  async clone(
    templateId: string,
    tenantId: string,
    newName: string,
  ): Promise<ParsingTemplate> {
    const original = await this.templateRepo.findOne({
      where: { id: templateId },
    });

    if (!original) {
      throw new Error(`Template ${templateId} not found`);
    }

    const cloned = this.templateRepo.create({
      tenant_id: tenantId,
      name: newName,
      template_category: original.template_category,
      detection: { ...original.detection },
      mappings: { ...original.mappings },
      notes: `Cloned from: ${original.name}`,
      created_by: tenantId,
      usage_count: 0,
    });

    return this.templateRepo.save(cloned);
  }

  /**
   * Get template statistics
   */
  async getStatistics(
    tenantId: string,
  ): Promise<{
    total: number;
    by_category: Record<string, number>;
    most_used: ParsingTemplate[];
  }> {
    const templates = await this.findAll(tenantId);

    const byCategory: Record<string, number> = {};
    for (const template of templates) {
      const cat = template.template_category || 'unknown';
      byCategory[cat] = (byCategory[cat] || 0) + 1;
    }

    const mostUsed = templates
      .filter((t) => t.usage_count > 0)
      .sort((a, b) => b.usage_count - a.usage_count)
      .slice(0, 5);

    return {
      total: templates.length,
      by_category: byCategory,
      most_used: mostUsed,
    };
  }
}
