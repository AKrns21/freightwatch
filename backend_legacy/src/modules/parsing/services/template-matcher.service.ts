import { IsNull } from 'typeorm';
import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { ParsingTemplate } from '@/modules/parsing/entities/parsing-template.entity';
import { Upload } from '@/modules/upload/entities/upload.entity';

/**
 * Template match result
 */
export interface TemplateMatch {
  template: ParsingTemplate;
  confidence: number;
  reasons: string[];
}

/**
 * TemplateMatcherService - Find matching parsing templates
 *
 * Matches uploaded files against known parsing templates to enable
 * automatic parsing without LLM analysis.
 */
@Injectable()
export class TemplateMatcherService {
  private readonly logger = new Logger(TemplateMatcherService.name);

  constructor(
    @InjectRepository(ParsingTemplate)
    private readonly templateRepo: Repository<ParsingTemplate>
  ) {}

  /**
   * Find best matching template for an upload
   */
  async findMatch(
    upload: Upload,
    tenantId: string,
    fileContent?: string
  ): Promise<TemplateMatch | null> {
    this.logger.log({
      event: 'template_match_start',
      upload_id: upload.id,
      filename: upload.filename,
      mime_type: upload.mime_type,
    });

    // Get applicable templates (global + tenant-specific)
    const templates = await this.getApplicableTemplates(tenantId, upload.mime_type);

    if (templates.length === 0) {
      this.logger.log({
        event: 'template_match_none_found',
        upload_id: upload.id,
      });
      return null;
    }

    // Extract file characteristics
    const characteristics = await this.extractCharacteristics(upload, fileContent);

    // Score each template
    const scored = templates.map((template) => {
      const score = this.scoreTemplate(template, characteristics);
      return { template, score };
    });

    // Sort by score descending
    scored.sort((a, b) => b.score.confidence - a.score.confidence);

    const best = scored[0];

    // Only return match if confidence is above threshold
    if (best.score.confidence < 0.7) {
      this.logger.log({
        event: 'template_match_low_confidence',
        upload_id: upload.id,
        best_confidence: best.score.confidence,
        template_id: best.template.id,
      });
      return null;
    }

    this.logger.log({
      event: 'template_match_found',
      upload_id: upload.id,
      template_id: best.template.id,
      template_name: best.template.name,
      confidence: best.score.confidence,
    });

    // Update template usage stats
    await this.updateTemplateUsage(best.template.id);

    return {
      template: best.template,
      confidence: best.score.confidence,
      reasons: best.score.reasons,
    };
  }

  /**
   * Get templates applicable to this tenant and file type
   */
  private async getApplicableTemplates(
    tenantId: string,
    mimeType: string
  ): Promise<ParsingTemplate[]> {
    // Get both global (tenant_id IS NULL) and tenant-specific templates
    const templates = await this.templateRepo
      .createQueryBuilder('template')
      .where('template.deleted_at IS NULL')
      .andWhere('(template.tenant_id IS NULL OR template.tenant_id = :tenantId)', { tenantId })
      .orderBy('template.usage_count', 'DESC')
      .getMany();

    // Filter by mime type compatibility
    return templates.filter((t) => this.isMimeTypeCompatible(t, mimeType));
  }

  /**
   * Check if template supports this mime type
   */
  private isMimeTypeCompatible(template: ParsingTemplate, mimeType: string): boolean {
    if (!template.detection?.mime_types) {
      return true; // No restriction
    }

    return template.detection.mime_types.some((pattern: string) => {
      // Support wildcards like "text/*"
      if (pattern.includes('*')) {
        const regex = new RegExp(pattern.replace('*', '.*'));
        return regex.test(mimeType);
      }
      return mimeType.includes(pattern);
    });
  }

  /**
   * Extract file characteristics for matching
   */
  private async extractCharacteristics(
    upload: Upload,
    fileContent?: string
  ): Promise<{
    filename: string;
    mimeType: string;
    headers?: string[];
    firstLines?: string[];
  }> {
    const characteristics = {
      filename: upload.filename,
      mimeType: upload.mime_type,
    };

    // If file content provided, extract headers and first lines
    if (fileContent) {
      const lines = fileContent.split('\n').slice(0, 10);

      // Try to detect headers (assume first line is header if comma-separated)
      if (lines[0]?.includes(',') || lines[0]?.includes(';')) {
        const separator = lines[0].includes(';') ? ';' : ',';
        (characteristics as any)['headers'] = lines[0].split(separator).map((h) => h.trim());
      }

      (characteristics as any)['firstLines'] = lines;
    }

    return characteristics;
  }

  /**
   * Score a template against file characteristics
   */
  private scoreTemplate(
    template: ParsingTemplate,
    characteristics: {
      filename: string;
      mimeType: string;
      headers?: string[];
      firstLines?: string[];
    }
  ): { confidence: number; reasons: string[] } {
    let score = 0;
    const reasons: string[] = [];

    // File type check (30%)
    if (template.detection.mime_types?.includes(characteristics.mimeType)) {
      score += 0.3;
      reasons.push('MIME type match');
    }

    // Filename pattern check (20%)
    if (template.detection.filename_pattern) {
      try {
        const regex = new RegExp(template.detection.filename_pattern, 'i');
        if (regex.test(characteristics.filename)) {
          score += 0.2;
          reasons.push('Filename pattern match');
        }
      } catch (error) {
        this.logger.warn(
          `Invalid regex in template ${template.id}: ${template.detection.filename_pattern}`
        );
      }
    }

    // Header keywords check (50%)
    if (template.detection.header_keywords && characteristics.headers) {
      const matchedKeywords = template.detection.header_keywords.filter((keyword: string) =>
        characteristics.headers?.some((header: string) =>
          header.toLowerCase().includes(keyword.toLowerCase())
        )
      );

      if (template.detection.header_keywords.length > 0) {
        const headerScore =
          (matchedKeywords.length / template.detection.header_keywords.length) * 0.5;
        score += headerScore;

        if (matchedKeywords.length > 0) {
          reasons.push(
            `${matchedKeywords.length}/${template.detection.header_keywords.length} header keywords matched`
          );
        }
      }
    }

    // Content pattern check (bonus, up to 10%)
    if (template.detection.content_patterns && characteristics.firstLines) {
      const contentText = characteristics.firstLines.join('\n');
      let patternMatches = 0;

      for (const pattern of template.detection.content_patterns) {
        try {
          const regex = new RegExp(pattern, 'i');
          if (regex.test(contentText)) {
            patternMatches++;
          }
        } catch (error) {
          this.logger.warn(`Invalid content pattern in template ${template.id}: ${pattern}`);
        }
      }

      if (template.detection.content_patterns.length > 0) {
        const contentScore = (patternMatches / template.detection.content_patterns.length) * 0.1;
        score += contentScore;

        if (patternMatches > 0) {
          reasons.push(`${patternMatches} content patterns matched`);
        }
      }
    }

    // Boost score for tenant-specific templates (10% bonus)
    if (template.tenant_id !== null) {
      score = Math.min(1.0, score * 1.1);
      reasons.push('Tenant-specific template');
    }

    // Boost score for frequently used templates (5% bonus)
    if (template.usage_count > 10) {
      score = Math.min(1.0, score * 1.05);
      reasons.push('Frequently used template');
    }

    return {
      confidence: Math.min(score, 1.0),
      reasons,
    };
  }

  /**
   * Update template usage statistics
   */
  private async updateTemplateUsage(templateId: string): Promise<void> {
    await this.templateRepo.increment({ id: templateId }, 'usage_count', 1);

    await this.templateRepo.update(templateId, {
      last_used_at: new Date(),
    });
  }

  /**
   * Get templates by category
   */
  async getTemplatesByCategory(tenantId: string, category: string): Promise<ParsingTemplate[]> {
    return this.templateRepo.find({
      where: [
        { tenant_id: IsNull(), template_category: category, deleted_at: IsNull() },
        { tenant_id: tenantId, template_category: category, deleted_at: IsNull() },
      ],
      order: { usage_count: 'DESC' },
    });
  }

  /**
   * Get all templates for a tenant
   */
  async getAllTemplates(tenantId: string): Promise<ParsingTemplate[]> {
    return this.templateRepo.find({
      where: [
        { tenant_id: IsNull(), deleted_at: IsNull() },
        { tenant_id: tenantId, deleted_at: IsNull() },
      ],
      order: { usage_count: 'DESC', created_at: 'DESC' },
    });
  }
}
