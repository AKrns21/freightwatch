import { Entity, Column, PrimaryGeneratedColumn, CreateDateColumn } from 'typeorm';

/**
 * Tenant — one logistics consultancy client.
 *
 * Each tenant is fully isolated via PostgreSQL Row Level Security (RLS).
 * The consultancy configures tenants; clients may get read-only access.
 *
 * Key configuration fields added by migrations:
 *   - data_retention_years (migration 010): GoBD retention period, default 10 years
 *   - freight_delta_threshold_pct (migration 014): invoice benchmark sensitivity
 */
@Entity('tenant')
export class Tenant {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ length: 255 })
  name: string;

  /**
   * Freeform settings JSONB (currency, country, default_diesel_pct, etc.).
   * Strongly-typed fields are added as dedicated columns via migrations.
   */
  @Column({ type: 'jsonb', default: {} })
  settings: Record<string, unknown>;

  /**
   * GoBD retention period in years (default: 10).
   * Used to compute raw_extraction.retain_until at insert time.
   * GoBD §14b requires 10 years for invoices in DE.
   */
  @Column({ type: 'int', default: 10 })
  data_retention_years: number;

  /**
   * Delta threshold for invoice benchmark classification (default: 5.0%).
   * Benchmark engine classification:
   *   delta_pct < -threshold  → 'unter'    (undercharged)
   *   within ±threshold       → 'im_markt' (within tolerance)
   *   delta_pct > threshold   → 'drüber'   (overcharged — flag for review)
   */
  @Column({ type: 'decimal', precision: 5, scale: 2, default: 5.0 })
  freight_delta_threshold_pct: number;

  @CreateDateColumn()
  created_at: Date;
}
