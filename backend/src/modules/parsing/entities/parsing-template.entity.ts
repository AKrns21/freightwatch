import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  DeleteDateColumn,
  Index,
} from 'typeorm';

/**
 * ParsingTemplate entity - Reusable file format definitions
 *
 * Templates define how to detect and parse specific file formats.
 * Can be global (tenant_id = NULL) or tenant-specific.
 * Templates learn from LLM suggestions and manual corrections.
 */
@Entity('parsing_template')
export class ParsingTemplate {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', nullable: true, comment: 'NULL = global template' })
  @Index()
  tenant_id: string | null;

  @Column({ length: 255 })
  name: string;

  @Column({ type: 'text', nullable: true })
  description: string;

  @Column({
    length: 50,
    comment: 'csv, excel, pdf, xml, json'
  })
  @Index()
  file_type: string;

  @Column({
    length: 50,
    nullable: true,
    comment: 'shipment_list, invoice, tariff, route_documentation'
  })
  @Index()
  template_category: string;

  @Column({
    type: 'jsonb',
    comment: 'Detection rules: file patterns, header keywords, structure checks'
  })
  detection: Record<string, any>;

  @Column({
    type: 'jsonb',
    comment: 'Field mappings: column -> database field transformations'
  })
  mappings: Record<string, any>;

  @Column({
    length: 50,
    default: 'manual',
    comment: 'manual, llm_suggested, system'
  })
  source: string;

  @Column({ type: 'uuid', nullable: true })
  verified_by: string;

  @Column({ type: 'timestamptz', nullable: true })
  verified_at: Date;

  @Column({ type: 'int', default: 0 })
  usage_count: number;

  @Column({ type: 'timestamptz', nullable: true })
  last_used_at: Date;

  @Column({ type: 'text', nullable: true })
  notes: string;

  @Column({ type: 'uuid', nullable: true })
  created_by: string;

  @CreateDateColumn()
  created_at: Date;

  @DeleteDateColumn()
  deleted_at: Date | null;

  /**
   * Convert to safe object for API responses
   */
  toSafeObject() {
    return {
      id: this.id,
      tenant_id: this.tenant_id,
      name: this.name,
      description: this.description,
      file_type: this.file_type,
      template_category: this.template_category,
      detection: this.detection,
      mappings: this.mappings,
      source: this.source,
      verified_by: this.verified_by,
      verified_at: this.verified_at,
      usage_count: this.usage_count,
      last_used_at: this.last_used_at,
      notes: this.notes,
      created_by: this.created_by,
      created_at: this.created_at,
      deleted_at: this.deleted_at,
    };
  }

  /**
   * Check if this template matches the given file characteristics
   */
  matches(fileCharacteristics: {
    filename: string;
    mimeType: string;
    headers?: string[];
    firstLines?: string[];
  }): number {
    let score = 0;

    // File type check
    if (this.detection.mime_types?.includes(fileCharacteristics.mimeType)) {
      score += 0.3;
    }

    // Filename pattern check
    if (this.detection.filename_pattern) {
      const regex = new RegExp(this.detection.filename_pattern, 'i');
      if (regex.test(fileCharacteristics.filename)) {
        score += 0.2;
      }
    }

    // Header keywords check
    if (this.detection.header_keywords && fileCharacteristics.headers) {
      const matchedKeywords = this.detection.header_keywords.filter((keyword: string) =>
        fileCharacteristics.headers?.some((header: string) =>
          header.toLowerCase().includes(keyword.toLowerCase())
        )
      );
      score += (matchedKeywords.length / this.detection.header_keywords.length) * 0.5;
    }

    return Math.min(score, 1.0);
  }
}
