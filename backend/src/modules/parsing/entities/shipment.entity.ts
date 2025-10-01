import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Carrier } from '../../upload/entities/carrier.entity';

@Entity('shipment')
export class Shipment {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  upload_id: string;

  @Column('uuid', { nullable: true })
  invoice_line_id: string;

  @Column({ type: 'date' })
  date: Date;

  @Column('uuid', { nullable: true })
  carrier_id: string;

  @ManyToOne(() => Carrier, { nullable: true })
  @JoinColumn({ name: 'carrier_id' })
  carrier: Carrier;

  @Column({ length: 50, nullable: true })
  service_level: string;

  @Column({ length: 100, nullable: true })
  reference_number: string;

  @Column({ length: 10, nullable: true })
  origin_zip: string;

  @Column({ length: 2, default: 'DE' })
  origin_country: string;

  @Column({ length: 10, nullable: true })
  dest_zip: string;

  @Column({ length: 2, default: 'DE' })
  dest_country: string;

  @Column({ type: 'integer', nullable: true })
  zone_de: number;

  @Column({ type: 'integer', nullable: true })
  zone_at: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  weight_kg: number;

  @Column({ type: 'decimal', precision: 10, scale: 3, nullable: true })
  volume_cbm: number;

  @Column({ type: 'decimal', precision: 5, scale: 2, nullable: true })
  pallets: number;

  @Column({ type: 'decimal', precision: 5, scale: 2, nullable: true })
  length_m: number;

  @Column({ length: 20, nullable: true })
  chargeable_basis: string;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  chargeable_weight_kg: number;

  @Column({ type: 'char', length: 3, default: 'EUR' })
  currency: string;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  actual_total_amount: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  actual_base_amount: number;

  @Column({ type: 'decimal', precision: 5, scale: 2, nullable: true })
  diesel_pct: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  diesel_amount: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  toll_amount: number;

  @Column({ length: 2, nullable: true })
  toll_country: string;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  other_surcharge_amount: number;

  @Column({ type: 'numeric', precision: 18, scale: 8, nullable: true })
  fx_rate: number;

  @Column({ type: 'char', length: 3, nullable: true })
  report_currency: string;

  @Column({ type: 'jsonb', nullable: true })
  report_amounts_cached: any;

  @Column({ type: 'jsonb', nullable: true })
  source_data: any;

  @Column({ length: 50, nullable: true })
  extraction_method: string;

  @Column({ type: 'decimal', precision: 3, scale: 2, nullable: true })
  confidence_score: number;

  // New fields for quality tracking
  @Column({ type: 'uuid', nullable: true })
  project_id: string;

  @Column({
    type: 'decimal',
    precision: 3,
    scale: 2,
    nullable: true,
    comment: 'Data completeness score (0.00 - 1.00)'
  })
  completeness_score: number;

  @Column({
    type: 'text',
    array: true,
    nullable: true,
    comment: 'Array of missing required field names'
  })
  missing_fields: string[];

  @Column({
    type: 'jsonb',
    nullable: true,
    comment: 'Structured data quality issues'
  })
  data_quality_issues: any;

  @Column({
    type: 'text',
    nullable: true,
    comment: 'Consultant notes and annotations'
  })
  consultant_notes: string;

  @Column({
    type: 'boolean',
    default: false,
    comment: 'True if manually corrected by consultant'
  })
  manual_override: boolean;

  @CreateDateColumn()
  created_at: Date;

  toSafeObject() {
    return {
      id: this.id,
      tenant_id: this.tenant_id,
      upload_id: this.upload_id,
      invoice_line_id: this.invoice_line_id,
      date: this.date,
      carrier_id: this.carrier_id,
      service_level: this.service_level,
      reference_number: this.reference_number,
      origin_zip: this.origin_zip,
      origin_country: this.origin_country,
      dest_zip: this.dest_zip,
      dest_country: this.dest_country,
      zone_de: this.zone_de,
      zone_at: this.zone_at,
      weight_kg: this.weight_kg,
      volume_cbm: this.volume_cbm,
      pallets: this.pallets,
      length_m: this.length_m,
      chargeable_basis: this.chargeable_basis,
      chargeable_weight_kg: this.chargeable_weight_kg,
      currency: this.currency,
      actual_total_amount: this.actual_total_amount,
      actual_base_amount: this.actual_base_amount,
      diesel_pct: this.diesel_pct,
      diesel_amount: this.diesel_amount,
      toll_amount: this.toll_amount,
      toll_country: this.toll_country,
      other_surcharge_amount: this.other_surcharge_amount,
      fx_rate: this.fx_rate,
      report_currency: this.report_currency,
      report_amounts_cached: this.report_amounts_cached,
      source_data: this.source_data,
      extraction_method: this.extraction_method,
      confidence_score: this.confidence_score,
      project_id: this.project_id,
      completeness_score: this.completeness_score,
      missing_fields: this.missing_fields,
      data_quality_issues: this.data_quality_issues,
      consultant_notes: this.consultant_notes,
      manual_override: this.manual_override,
      created_at: this.created_at,
    };
  }
}