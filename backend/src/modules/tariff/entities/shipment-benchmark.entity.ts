import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Shipment } from '../../parsing/entities/shipment.entity';

@Entity('shipment_benchmark')
export class ShipmentBenchmark {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  shipment_id: string;

  @ManyToOne(() => Shipment)
  @JoinColumn({ name: 'shipment_id' })
  shipment: Shipment;

  @Column('uuid')
  tenant_id: string;

  @Column({ type: 'decimal', precision: 10, scale: 2 })
  expected_base_amount: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  expected_toll_amount: number | null;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  expected_diesel_amount: number | null;

  @Column({ type: 'decimal', precision: 10, scale: 2 })
  expected_total_amount: number;

  @Column({ type: 'decimal', precision: 10, scale: 2 })
  actual_total_amount: number;

  @Column({ type: 'decimal', precision: 10, scale: 2 })
  delta_amount: number;

  @Column({ type: 'decimal', precision: 5, scale: 2 })
  delta_pct: number;

  @Column({ length: 20 })
  classification: string;

  @Column({ type: 'char', length: 3 })
  currency: string;

  @Column({ type: 'char', length: 3, nullable: true })
  report_currency: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, nullable: true })
  fx_rate_used: number | null;

  @Column({ type: 'date', nullable: true })
  fx_rate_date: Date | null;

  @Column({ length: 20, nullable: true })
  diesel_basis_used: string | null;

  @Column({ type: 'decimal', precision: 5, scale: 2, nullable: true })
  diesel_pct_used: number | null;

  @Column({ type: 'jsonb' })
  cost_breakdown: any;

  @Column({ type: 'jsonb', nullable: true })
  report_amounts: any;

  @Column({ type: 'jsonb', nullable: true })
  calculation_metadata: any;

  @CreateDateColumn()
  created_at: Date;
}