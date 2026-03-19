import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
  DeleteDateColumn,
  OneToMany,
} from 'typeorm';
import { InvoiceLine } from './invoice-line.entity';

/**
 * InvoiceHeader Entity - Invoice metadata
 *
 * Stores header information from carrier invoices:
 * - Invoice number, date, amount
 * - Carrier and customer information
 * - Upload reference
 * - Parsing metadata
 */
@Entity('invoice_header')
export class InvoiceHeader {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid', { nullable: true })
  upload_id: string;

  @Column('uuid', { nullable: true })
  project_id: string;

  @Column({ length: 100 })
  invoice_number: string;

  @Column('date')
  invoice_date: Date;

  @Column('uuid', { nullable: true })
  carrier_id: string;

  @Column({ length: 255, nullable: true })
  carrier_name: string;

  @Column({ length: 255, nullable: true })
  customer_name: string;

  @Column({ length: 100, nullable: true })
  customer_number: string;

  @Column('decimal', { precision: 12, scale: 2, nullable: true })
  total_amount: number;

  @Column('char', { length: 3, default: 'EUR' })
  currency: string;

  @Column({ length: 50, nullable: true })
  payment_terms: string;

  @Column('date', { nullable: true })
  due_date: Date;

  @Column({
    type: 'jsonb',
    nullable: true,
    comment: 'Raw invoice data as parsed',
  })
  source_data: Record<string, unknown>;

  @Column({
    type: 'jsonb',
    default: {},
    comment: 'Additional metadata (parsing method, confidence, etc.)',
  })
  meta: Record<string, unknown>;

  @OneToMany(() => InvoiceLine, (line) => line.invoice)
  lines: InvoiceLine[];

  @CreateDateColumn()
  created_at: Date;

  @UpdateDateColumn()
  updated_at: Date;

  @DeleteDateColumn()
  deleted_at: Date;

  /**
   * Get safe object for API responses (exclude sensitive data)
   */
  toSafeObject(): Omit<InvoiceHeader, 'source_data'> {
    const { source_data: _source_data, ...safe } = this;
    return safe as Omit<InvoiceHeader, 'source_data'>;
  }
}
