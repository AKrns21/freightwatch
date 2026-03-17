import { Entity, Column, PrimaryGeneratedColumn, CreateDateColumn, OneToMany } from 'typeorm';
import { TariffRate } from './tariff-rate.entity';

@Entity('tariff_table')
export class TariffTable {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  carrier_id: string;

  @Column({ length: 100 })
  name: string;

  @Column({ length: 20 })
  lane_type: string;

  @Column({ type: 'char', length: 3, default: 'EUR' })
  currency: string;

  @Column({ type: 'date' })
  valid_from: Date;

  @Column({ type: 'date', nullable: true })
  valid_until: Date | null;

  @Column({ type: 'decimal', precision: 3, scale: 2, nullable: true, comment: 'Parsing confidence (0.00–1.00)' })
  confidence: number | null;

  @Column({ type: 'jsonb', nullable: true, comment: 'Parsing metadata: parsing_method, parsing_issues[]' })
  source_data: Record<string, unknown> | null;

  @CreateDateColumn()
  created_at: Date;

  @OneToMany(() => TariffRate, (rate) => rate.tariff_table)
  rates: TariffRate[];
}
