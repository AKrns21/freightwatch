import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  UpdateDateColumn,
} from 'typeorm';

@Entity('upload')
export class Upload {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column({ length: 500 })
  filename: string;

  @Column({ length: 64, comment: 'SHA256 hash for deduplication' })
  file_hash: string;

  @Column({ length: 100, nullable: true })
  mime_type: string;

  @Column({ length: 50, nullable: true, comment: 'invoice, rate_card, fleet_log' })
  source_type: string;

  @Column({ type: 'timestamptz', default: () => 'now()' })
  received_at: Date;

  @Column({ type: 'text', nullable: true })
  storage_url: string;

  @Column({ length: 50, default: 'pending', comment: 'pending, parsed, unmatched, failed' })
  status: string;

  @Column({ type: 'jsonb', nullable: true })
  parse_errors: unknown;

  @Column({ length: 64, nullable: true, comment: 'For unmatched carrier templates' })
  raw_text_hash: string;

  // New fields for project-based workflow
  @Column({ type: 'uuid', nullable: true })
  project_id: string;

  @Column({
    length: 50,
    nullable: true,
    comment: 'template, llm, manual, hybrid',
  })
  parse_method: string;

  @Column({
    type: 'decimal',
    precision: 3,
    scale: 2,
    nullable: true,
    comment: 'Parsing confidence (0.00 - 1.00)',
  })
  confidence: number;

  @Column({
    type: 'jsonb',
    nullable: true,
    comment: 'LLM-suggested column mappings',
  })
  suggested_mappings: unknown;

  @Column({
    type: 'jsonb',
    nullable: true,
    comment: 'Complete LLM analysis result',
  })
  llm_analysis: unknown;

  @Column({ type: 'uuid', nullable: true })
  reviewed_by: string;

  @Column({ type: 'timestamptz', nullable: true })
  reviewed_at: Date;

  @Column({
    type: 'jsonb',
    nullable: true,
    comment: 'Array of parsing issues found',
  })
  parsing_issues: unknown;

  @Column({
    type: 'jsonb',
    nullable: true,
    default: '{}',
    comment: 'Additional metadata (review info, reprocess info, etc.)',
  })
  meta: unknown;

  @CreateDateColumn()
  created_at: Date;

  @UpdateDateColumn()
  updated_at: Date;

  toSafeObject(): Record<string, unknown> {
    return {
      id: this.id,
      tenant_id: this.tenant_id,
      filename: this.filename,
      file_hash: this.file_hash,
      mime_type: this.mime_type,
      source_type: this.source_type,
      received_at: this.received_at,
      storage_url: this.storage_url,
      status: this.status,
      parse_errors: this.parse_errors,
      raw_text_hash: this.raw_text_hash,
      project_id: this.project_id,
      parse_method: this.parse_method,
      confidence: this.confidence,
      suggested_mappings: this.suggested_mappings,
      llm_analysis: this.llm_analysis,
      reviewed_by: this.reviewed_by,
      reviewed_at: this.reviewed_at,
      parsing_issues: this.parsing_issues,
      meta: this.meta,
      created_at: this.created_at,
      updated_at: this.updated_at,
    };
  }
}
