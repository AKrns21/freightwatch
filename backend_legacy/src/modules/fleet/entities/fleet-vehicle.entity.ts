import { Entity, Column, PrimaryGeneratedColumn } from 'typeorm';

/**
 * FleetVehicle — cost-model master data for own delivery vehicles.
 *
 * Used exclusively for the Own vs. Carrier Benchmark cost model (§5.2).
 * Separate from the telemetry-focused `vehicle` table (plate/type tracking).
 *
 * Cost model contribution:
 *   fixed_cost = fixed_cost_per_day  (EUR/day, regardless of usage)
 *   fuel_cost  = own_tour.distance_km × variable_cost_per_km
 */
@Entity('fleet_vehicle')
export class FleetVehicle {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column({ type: 'text' })
  license_plate: string;

  /** e.g. 'sprinter' | '7.5t' | '12t' | '40t' */
  @Column({ type: 'text', nullable: true })
  vehicle_type: string | null;

  @Column({ type: 'int', nullable: true })
  payload_kg: number | null;

  /** Fixed daily cost in EUR regardless of km driven. */
  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  fixed_cost_per_day: number | null;

  /** Variable cost per km driven (fuel, maintenance). */
  @Column({ type: 'decimal', precision: 8, scale: 4, nullable: true })
  variable_cost_per_km: number | null;

  @Column({ type: 'char', length: 3, default: 'EUR' })
  currency: string;

  @Column({ type: 'boolean', default: true })
  active: boolean;
}
