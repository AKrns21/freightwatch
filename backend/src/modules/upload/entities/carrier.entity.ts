import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  UpdateDateColumn,
} from 'typeorm';

/**
 * Carrier entity - Freight carriers/forwarders
 *
 * Represents logistics carriers and their configuration.
 * Supports both global carriers (tenant_id = NULL) and tenant-specific carriers.
 */
@Entity('carrier')
export class Carrier {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', nullable: true, comment: 'NULL = global carrier' })
  tenant_id: string;

  @Column({ length: 255 })
  name: string;

  @Column({ length: 50, unique: true })
  code_norm: string;

  @Column({ length: 2, nullable: true })
  country: string;

  /**
   * Conversion rules (replaces tariff_rule table)
   * Stores carrier-specific calculation rules as JSONB
   *
   * Example structure:
   * {
   *   "ldm_conversion": { "ldm_to_kg": 1850 },
   *   "min_pallet_weight": { "min_kg_per_pallet": 300 },
   *   "weight_rounding": { "round_to": 10, "method": "up" }
   * }
   */
  @Column({
    type: 'jsonb',
    default: {},
    comment: 'Carrier-specific calculation rules (replaces tariff_rule table)',
  })
  conversion_rules: Record<string, unknown>;

  @CreateDateColumn()
  created_at: Date;

  @UpdateDateColumn()
  updated_at: Date;

  /**
   * Get a specific conversion rule
   */
  getRule(ruleType: string): unknown | null {
    return this.conversion_rules?.[ruleType] ?? null;
  }

  /**
   * Set a conversion rule
   */
  setRule(ruleType: string, ruleParams: unknown): void {
    if (!this.conversion_rules) {
      this.conversion_rules = {};
    }
    this.conversion_rules[ruleType] = ruleParams;
  }

  /**
   * Convert to safe object for API responses
   */
  toSafeObject(): Record<string, unknown> {
    return {
      id: this.id,
      tenant_id: this.tenant_id,
      name: this.name,
      code_norm: this.code_norm,
      country: this.country,
      conversion_rules: this.conversion_rules,
      created_at: this.created_at,
      updated_at: this.updated_at,
    };
  }
}
