import { Entity, Column, PrimaryGeneratedColumn, ManyToOne, JoinColumn } from 'typeorm';
import { Upload } from './upload.entity';

/**
 * ExtractionCorrection — field-level human correction audit trail.
 *
 * Every time a consultant corrects a field in the review UI, a row is
 * written here. This serves three purposes:
 *
 *   1. Audit trail (GoBD): proves what was changed, by whom, and when
 *   2. Prompt improvement: corrections are fed back into extraction prompts
 *      to reduce the same error in future documents from the same carrier
 *   3. Re-normalization: saving a correction triggers re-processing of the
 *      associated raw_extraction payload against the normalized tables
 *
 * field_path uses JSON path notation:
 *   "header.invoice_date"     ← top-level header field
 *   "lines[3].weight_kg"      ← field on a specific line item
 *   "lines[3].dest_zip"       ← address field on a line
 */
@Entity('extraction_correction')
export class ExtractionCorrection {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  upload_id: string;

  /**
   * JSON path to the corrected field.
   * Examples: "header.invoice_date" | "lines[3].weight_kg"
   */
  @Column({ type: 'text' })
  field_path: string;

  /** Value as originally extracted by LLM/parser. NULL if the field was missing. */
  @Column({ type: 'text', nullable: true })
  original_value: string | null;

  /** Value as corrected by the consultant. */
  @Column({ type: 'text' })
  corrected_value: string;

  /** User ID of the consultant who made the correction. */
  @Column({ type: 'uuid', nullable: true })
  corrected_by: string | null;

  @Column({ type: 'timestamptz', default: () => 'now()' })
  corrected_at: Date;

  @ManyToOne(() => Upload)
  @JoinColumn({ name: 'upload_id' })
  upload: Upload;
}
