import { Entity, Column, PrimaryGeneratedColumn, ManyToOne, JoinColumn } from 'typeorm';
import { TariffTable } from './tariff-table.entity';

/**
 * TariffSurcharge — flexible catch-all for non-standard surcharges.
 *
 * Additive to tariff_nebenkosten (keep both):
 * - tariff_nebenkosten: strongly typed columns for known Nebenkosten fields
 *   (diesel_floater_pct, maut_basis, avis_fee, etc.) — queried directly by benchmark engine
 * - tariff_surcharge: catch-all for anything that does NOT have a typed column
 *
 * Benchmark surcharge calculation:
 *   diesel  ← tariff_nebenkosten.diesel_floater_pct  (typed, direct)
 *   maut    ← tariff_nebenkosten.maut_basis + matrix  (typed, direct)
 *   other   ← SUM(tariff_surcharge WHERE applicable)  (flexible catch-all)
 */
@Entity('tariff_surcharge')
export class TariffSurcharge {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tariff_id: string;

  @Column('uuid')
  tenant_id: string;

  /**
   * Type of surcharge.
   * Examples: 'diesel_floater' | 'avis' | 'manual_order' | 'pallet_exchange' | 'customs_clearance'
   */
  @Column({ type: 'text' })
  surcharge_type: string;

  /**
   * How the surcharge is calculated.
   * Values: 'per_shipment' | 'pct_of_base' | 'flat'
   */
  @Column({ type: 'text', nullable: true })
  basis: string | null;

  @Column({ type: 'decimal', precision: 12, scale: 4, nullable: true })
  value: number | null;

  @Column({ type: 'char', length: 3, default: 'EUR' })
  currency: string;

  @Column({ type: 'text', nullable: true })
  notes: string | null;

  @ManyToOne(() => TariffTable)
  @JoinColumn({ name: 'tariff_id' })
  tariff_table: TariffTable;
}
