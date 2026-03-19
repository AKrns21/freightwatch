import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  DeleteDateColumn,
  Index,
} from 'typeorm';

/**
 * ManualMapping entity - Override automatic mapping decisions
 *
 * Stores consultant corrections to LLM or template-based mappings.
 * Used to improve future template suggestions and track manual interventions.
 */
@Entity('manual_mapping')
export class ManualMapping {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  @Index()
  upload_id: string;

  @Column({
    length: 100,
    comment: 'Database field name (e.g., origin_zip, carrier_name)',
  })
  @Index()
  field_name: string;

  @Column({ length: 100, nullable: true })
  source_column: string;

  @Column({
    type: 'jsonb',
    nullable: true,
    comment: 'Transformation rule applied to source data',
  })
  mapping_rule: Record<string, any>;

  @Column({
    type: 'decimal',
    precision: 3,
    scale: 2,
    nullable: true,
    comment: 'Confidence in this mapping (0.00 - 1.00)',
  })
  confidence: number;

  @Column({ type: 'text', nullable: true })
  notes: string;

  @Column('uuid')
  created_by: string;

  @CreateDateColumn()
  created_at: Date;

  @DeleteDateColumn()
  deleted_at: Date;

  /**
   * Convert to safe object for API responses
   */
  toSafeObject() {
    return {
      id: this.id,
      upload_id: this.upload_id,
      field_name: this.field_name,
      source_column: this.source_column,
      mapping_rule: this.mapping_rule,
      confidence: this.confidence,
      notes: this.notes,
      created_by: this.created_by,
      created_at: this.created_at,
      deleted_at: this.deleted_at,
    };
  }
}
