import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { TariffTable } from './tariff-table.entity';

@Entity('tariff_rate')
export class TariffRate {
  @PrimaryGeneratedColumn('uuid')
  id!: string;

  @Column('uuid')
  tariff_table_id!: string;

  @Column({ type: 'integer' })
  zone!: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  weight_from_kg!: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  weight_to_kg!: number;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  rate_per_shipment!: number | null;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  rate_per_kg!: number | null;

  @ManyToOne(() => TariffTable, table => table.rates)
  @JoinColumn({ name: 'tariff_table_id' })
  tariff_table!: TariffTable;
}