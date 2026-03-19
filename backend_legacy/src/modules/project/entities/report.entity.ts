import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  DeleteDateColumn,
  Unique,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Project } from './project.entity';

/**
 * Report entity - Versioned report snapshots
 *
 * Each report generation creates a new version, allowing consultants
 * to track changes as data quality improves and analysis progresses.
 */
@Entity('report')
@Unique(['project_id', 'version'])
export class Report {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  project_id: string;

  @Column('int')
  version: number;

  @Column({
    length: 50,
    comment: 'quick_check, deep_dive, final',
  })
  report_type: string;

  @Column({ length: 255, nullable: true })
  title: string;

  @Column({
    type: 'jsonb',
    comment: 'Complete snapshot of aggregated data at generation time',
  })
  data_snapshot: Record<string, any>;

  @Column({
    type: 'decimal',
    precision: 3,
    scale: 2,
    nullable: true,
    comment: 'Data completeness score (0.00 - 1.00)',
  })
  data_completeness: number;

  @Column({ type: 'int', nullable: true })
  shipment_count: number;

  @Column({ type: 'date', nullable: true })
  date_range_start: Date;

  @Column({ type: 'date', nullable: true })
  date_range_end: Date;

  @Column('uuid')
  generated_by: string;

  @Column({ type: 'timestamptz', default: () => 'now()' })
  generated_at: Date;

  @CreateDateColumn()
  created_at: Date;

  @Column({ type: 'text', nullable: true })
  notes: string;

  @DeleteDateColumn()
  deleted_at: Date;

  // Relations
  @ManyToOne(() => Project, (project: Project) => project.reports)
  @JoinColumn({ name: 'project_id' })
  project: Project;

  /**
   * Convert to safe object for API responses
   */
  toSafeObject() {
    return {
      id: this.id,
      project_id: this.project_id,
      version: this.version,
      report_type: this.report_type,
      title: this.title,
      data_snapshot: this.data_snapshot,
      data_completeness: this.data_completeness,
      shipment_count: this.shipment_count,
      date_range_start: this.date_range_start,
      date_range_end: this.date_range_end,
      generated_by: this.generated_by,
      generated_at: this.generated_at,
      created_at: this.created_at,
      notes: this.notes,
      deleted_at: this.deleted_at,
    };
  }
}
