import { Entity, Column, PrimaryGeneratedColumn, ManyToOne, JoinColumn } from 'typeorm';
import { TariffTable } from './tariff-table.entity';

export type FtlRateBasis = 'per_km' | 'per_day' | 'flat_tour';

/**
 * TariffFtlRate — FTL/Charter rate table.
 *
 * Used for full-truckload and charter shipments that are priced by km, day,
 * or flat tour — not by the zone × weight matrix (tariff_rate).
 *
 * The benchmark engine branches on invoice_line.service_type:
 *   'stückgut' → tariff_rate (zone × weight)
 *   'ftl' | 'charter' → tariff_ftl_rate
 *
 * Rate calculation by rate_basis:
 *   per_km    : expected = MAX(price × distance_km, min_price)
 *   per_day   : expected = MAX(price × days, min_price)
 *   flat_tour : expected = MAX(price, min_price)
 */
@Entity('tariff_ftl_rate')
export class TariffFtlRate {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tariff_id: string;

  @Column('uuid')
  tenant_id: string;

  /** How the rate is applied: 'per_km' | 'per_day' | 'flat_tour' */
  @Column({ type: 'text' })
  rate_basis: FtlRateBasis;

  /** Optional scope — e.g. '7.5t' | '12t' | '40t'. NULL matches any vehicle. */
  @Column({ type: 'text', nullable: true })
  vehicle_type: string | null;

  /** Optional scope — PLZ prefix or free-text region label. NULL matches any destination. */
  @Column({ type: 'text', nullable: true })
  dest_region: string | null;

  @Column({ type: 'decimal', precision: 12, scale: 4 })
  price: number;

  @Column({ type: 'char', length: 3, default: 'EUR' })
  currency: string;

  /** Minimum charge per tour — guards against very short trips on per_km rates. */
  @Column({ type: 'decimal', precision: 12, scale: 4, nullable: true })
  min_price: number | null;

  @Column({ type: 'text', nullable: true })
  notes: string | null;

  @ManyToOne(() => TariffTable)
  @JoinColumn({ name: 'tariff_id' })
  tariff_table: TariffTable;
}
