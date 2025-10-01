import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  DeleteDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Project } from './project.entity';

/**
 * ConsultantNote entity - Annotations and quality issues
 *
 * Allows consultants to document data quality issues, missing information,
 * and action items during the review process.
 */
@Entity('consultant_note')
export class ConsultantNote {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  project_id: string;

  @Column({
    length: 50,
    comment: 'data_quality, missing_info, action_item, clarification, observation'
  })
  note_type: string;

  @Column('text')
  content: string;

  @Column({ type: 'uuid', nullable: true })
  related_to_upload_id: string;

  @Column({ type: 'uuid', nullable: true })
  related_to_shipment_id: string;

  @Column({
    length: 20,
    nullable: true,
    comment: 'low, medium, high, critical'
  })
  priority: string;

  @Column({ length: 50, default: 'open', comment: 'open, in_progress, resolved, closed' })
  status: string;

  @Column('uuid')
  created_by: string;

  @CreateDateColumn()
  created_at: Date;

  @Column({ type: 'timestamptz', nullable: true })
  resolved_at: Date;

  @DeleteDateColumn()
  deleted_at: Date;

  // Relations
  @ManyToOne(() => Project, project => project.notes)
  @JoinColumn({ name: 'project_id' })
  project: Project;

  /**
   * Convert to safe object for API responses
   */
  toSafeObject() {
    return {
      id: this.id,
      project_id: this.project_id,
      note_type: this.note_type,
      content: this.content,
      related_to_upload_id: this.related_to_upload_id,
      related_to_shipment_id: this.related_to_shipment_id,
      priority: this.priority,
      status: this.status,
      created_by: this.created_by,
      created_at: this.created_at,
      resolved_at: this.resolved_at,
      deleted_at: this.deleted_at,
    };
  }
}
