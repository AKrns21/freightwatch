import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Upload } from './upload.entity';

/**
 * RawExtraction — data lake audit trail for every parsed document.
 *
 * Stores the full raw output of each LLM / parser run BEFORE normalization.
 * This enables:
 *   - Re-processing: if parsing logic improves, re-run against stored payloads
 *   - Audit trail: proves how a document was interpreted (e.g. in carrier disputes)
 *   - GoBD §14b compliance: retain_until guarantees 10-year minimum retention
 *
 * Lifecycle:
 *   1. Parser/LLM produces raw JSON → stored here before normalization
 *   2. Normalization pipeline writes to invoice_header, tariff_table, etc.
 *   3. Human corrections (extraction_correction table) trigger re-normalization
 *   4. Nightly archival job flags rows where retain_until < CURRENT_DATE
 *      as eligible for deletion — but requires human sign-off (never auto-deleted)
 *
 * IMPORTANT: This record must NEVER be deleted within the GoBD retention window.
 */
@Entity('raw_extraction')
export class RawExtraction {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  upload_id: string;

  /**
   * Document type at time of extraction.
   * Values: 'invoice' | 'tariff' | 'shipment_csv' | 'tour'
   */
  @Column({ type: 'text' })
  doc_type: string;

  @Column({ type: 'timestamptz', default: () => 'now()' })
  extracted_at: Date;

  /**
   * Which parser / model produced this extraction.
   * Examples: 'claude-vision' | 'csv-parser' | 'template:carrier_xyz_v2'
   */
  @Column({ type: 'text' })
  extractor: string;

  /** Document-level confidence (0.000–1.000). NULL if not applicable (e.g. CSV). */
  @Column({ type: 'decimal', precision: 4, scale: 3, nullable: true })
  confidence: number | null;

  /** Full raw payload as produced by the LLM or parser, before any normalization. */
  @Column({ type: 'jsonb' })
  payload: Record<string, unknown>;

  /** Warnings and parse errors from the extraction step. */
  @Column({ type: 'text', array: true, nullable: true })
  issues: string[] | null;

  /** Whether this raw payload has been normalised into the core tables. */
  @Column({ type: 'boolean', default: false })
  normalized: boolean;

  @Column({ type: 'timestamptz', nullable: true })
  normalized_at: Date | null;

  /**
   * GoBD retention deadline — set at insert, never updated.
   * Computed as: extracted_at::date + tenant.data_retention_years years
   * The nightly archival job uses this to flag eligible-for-deletion rows.
   */
  @Column({ type: 'date' })
  retain_until: Date;

  @ManyToOne(() => Upload)
  @JoinColumn({ name: 'upload_id' })
  upload: Upload;
}
