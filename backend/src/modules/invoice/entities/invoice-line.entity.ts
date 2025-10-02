import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
  DeleteDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { InvoiceHeader } from './invoice-header.entity';

/**
 * InvoiceLine Entity - Individual invoice line items
 *
 * Stores line-level details from carrier invoices:
 * - Shipment reference, date, route
 * - Weight, service level
 * - Line amounts (base, surcharges, total)
 * - Matching status to shipments
 */
@Entity('invoice_line')
export class InvoiceLine {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  invoice_id: string;

  @ManyToOne(() => InvoiceHeader, (header: InvoiceHeader) => header.lines)
  @JoinColumn({ name: 'invoice_id' })
  invoice: InvoiceHeader;

  @Column('uuid', { nullable: true })
  shipment_id: string | null;

  @Column('int', { nullable: true })
  line_number: number;

  @Column('date', { nullable: true })
  shipment_date: Date;

  @Column({ length: 100, nullable: true })
  shipment_reference: string;

  @Column({ length: 10, nullable: true })
  origin_zip: string;

  @Column('char', { length: 2, nullable: true, default: 'DE' })
  origin_country: string;

  @Column({ length: 10, nullable: true })
  dest_zip: string;

  @Column('char', { length: 2, nullable: true, default: 'DE' })
  dest_country: string;

  @Column('decimal', { precision: 10, scale: 2, nullable: true })
  weight_kg: number;

  @Column({ length: 50, nullable: true })
  service_level: string;

  @Column('decimal', { precision: 10, scale: 2, nullable: true })
  base_amount: number;

  @Column('decimal', { precision: 10, scale: 2, nullable: true })
  diesel_amount: number;

  @Column('decimal', { precision: 10, scale: 2, nullable: true })
  toll_amount: number;

  @Column('decimal', { precision: 10, scale: 2, nullable: true })
  other_charges: number;

  @Column('decimal', { precision: 10, scale: 2, nullable: true })
  line_total: number;

  @Column('char', { length: 3, default: 'EUR' })
  currency: string;

  @Column({
    type: 'varchar',
    length: 20,
    nullable: true,
    comment: 'matched, unmatched, ambiguous, manual',
  })
  match_status: string;

  @Column('decimal', { precision: 3, scale: 2, nullable: true })
  match_confidence: number;

  @Column({
    type: 'jsonb',
    nullable: true,
    comment: 'Raw line data as parsed',
  })
  source_data: Record<string, any>;

  @Column({
    type: 'jsonb',
    default: {},
    comment: 'Matching details, manual corrections, etc.',
  })
  meta: Record<string, any>;

  @CreateDateColumn()
  created_at: Date;

  @UpdateDateColumn()
  updated_at: Date;

  @DeleteDateColumn()
  deleted_at: Date;

  /**
   * Get safe object for API responses
   */
  toSafeObject(): Partial<InvoiceLine> {
    const { source_data, ...safe } = this;
    return safe;
  }
}
