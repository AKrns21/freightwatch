import { Entity, Column, PrimaryGeneratedColumn, ManyToOne, JoinColumn } from 'typeorm';
import { TariffTable } from './tariff-table.entity';

export type SpecialConditionType =
  | 'fixed_price'
  | 'price_cap'
  | 'min_price'
  | 'pct_discount'
  | 'flat_tour';

/**
 * TariffSpecialCondition — Sonderkonditionen and Vereinbarungspreise.
 *
 * Overrides the standard tariff matrix. The benchmark engine checks this table
 * BEFORE the standard zone × weight lookup in tariff_rate.
 *
 * Lookup priority in benchmark engine:
 *   1. tariff_special_condition (condition_type = 'flat_tour')  ← Vereinbarungspreise
 *   2. tariff_special_condition (other types)                   ← Sonderkonditionen
 *   3. tariff_rate (standard zone × weight matrix)
 *
 * Example — Vereinbarungspreis LA 200 to PLZ 61118:
 *   condition_type = 'flat_tour'
 *   dest_zip_prefix = '61118'
 *   value = 530.00
 *   description = 'Vereinbarungspreis LA 200 Zone 8'
 */
@Entity('tariff_special_condition')
export class TariffSpecialCondition {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tariff_id: string;

  @Column('uuid')
  tenant_id: string;

  /**
   * fixed_price  : exact price regardless of zone/weight
   * price_cap    : maximum allowed charge
   * min_price    : minimum charge
   * pct_discount : percentage discount off the standard rate
   * flat_tour    : flat rate for a full tour (Vereinbarungspreis)
   */
  @Column({ type: 'text' })
  condition_type: SpecialConditionType;

  /** Destination ZIP scope — exact match ('61118') or prefix match ('61'). NULL = all. */
  @Column({ type: 'text', nullable: true })
  dest_zip_prefix: string | null;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  weight_from_kg: number | null;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  weight_to_kg: number | null;

  /** EUR for fixed/cap/min/flat_tour; percentage for pct_discount */
  @Column({ type: 'decimal', precision: 12, scale: 4 })
  value: number;

  /** Free text, e.g. "Sonderpreis Kunde Mecu Zone 8" */
  @Column({ type: 'text', nullable: true })
  description: string | null;

  @Column({ type: 'date' })
  valid_from: Date;

  @Column({ type: 'date', nullable: true })
  valid_until: Date | null;

  @ManyToOne(() => TariffTable)
  @JoinColumn({ name: 'tariff_id' })
  tariff_table: TariffTable;
}
