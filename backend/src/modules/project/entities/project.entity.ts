import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  UpdateDateColumn,
  DeleteDateColumn,
  OneToMany,
} from 'typeorm';
import { ConsultantNote } from './consultant-note.entity';
import { Report } from './report.entity';

/**
 * Project entity - Main workspace for freight cost analysis
 *
 * A project represents a complete analysis cycle from file upload
 * through review to final report generation.
 */
@Entity('project')
export class Project {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column({ length: 255 })
  name: string;

  @Column({ length: 255, nullable: true })
  customer_name: string;

  @Column({
    length: 50,
    default: 'quick_check',
    comment: 'quick_check, deep_dive, final_report',
  })
  phase: string;

  @Column({
    length: 50,
    default: 'draft',
    comment: 'draft, in_progress, review, completed, archived',
  })
  status: string;

  @Column({ type: 'uuid', nullable: true })
  consultant_id: string;

  @Column({ type: 'jsonb', default: {} })
  metadata: Record<string, any>;

  @CreateDateColumn()
  created_at: Date;

  @UpdateDateColumn()
  updated_at: Date;

  @DeleteDateColumn()
  deleted_at: Date;

  // Relations
  @OneToMany(() => ConsultantNote, (note) => note.project)
  notes: ConsultantNote[];

  @OneToMany(() => Report, (report) => report.project)
  reports: Report[];

  /**
   * Convert to safe object for API responses
   */
  toSafeObject() {
    return {
      id: this.id,
      tenant_id: this.tenant_id,
      name: this.name,
      customer_name: this.customer_name,
      phase: this.phase,
      status: this.status,
      consultant_id: this.consultant_id,
      metadata: this.metadata,
      created_at: this.created_at,
      updated_at: this.updated_at,
      deleted_at: this.deleted_at,
    };
  }
}
