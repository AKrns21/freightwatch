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
  parse_errors: any;

  @Column({ length: 64, nullable: true, comment: 'For unmatched carrier templates' })
  raw_text_hash: string;

  @CreateDateColumn()
  created_at: Date;

  @UpdateDateColumn()
  updated_at: Date;

  toSafeObject() {
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
      created_at: this.created_at,
      updated_at: this.updated_at,
    };
  }
}