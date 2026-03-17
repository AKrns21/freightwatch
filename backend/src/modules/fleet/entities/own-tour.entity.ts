import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  ManyToOne,
  OneToMany,
  JoinColumn,
  CreateDateColumn,
} from 'typeorm';
import { FleetVehicle } from './fleet-vehicle.entity';
import { FleetDriver } from './fleet-driver.entity';
import { OwnTourStop } from './own-tour-stop.entity';

/**
 * OwnTour — one logical delivery tour (departure from depot → return to depot).
 *
 * Aggregates individual stops into a tour. Used by the Own vs. Carrier
 * Benchmark module (§5.2) to compute total tour cost and cost-per-stop.
 *
 * Cost model (§5.2):
 *   fixed_cost  = fleet_vehicle.fixed_cost_per_day
 *   fuel_cost   = distance_km × fleet_vehicle.variable_cost_per_km
 *   driver_cost = duration_hours × fleet_driver.hourly_rate
 *   total       = fixed_cost + fuel_cost + driver_cost
 *   per_stop    = total / stop_count
 *
 * Note: legacy telemetry columns from route_trip (trip_date, total_km,
 * total_drive_min, cost_km, cost_time, cost_total, cost_per_stop, meta)
 * are preserved in the database but not mapped here — use them via raw queries
 * if needed until a full migration aligns the schema.
 */
@Entity('own_tour')
export class OwnTour {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column({ type: 'uuid', nullable: true })
  upload_id: string | null;

  /** External tour number from dispatcher/TMS (e.g. "24475"). */
  @Column({ type: 'text', nullable: true })
  tour_id: string | null;

  /** Tour date — maps to legacy trip_date column. */
  @Column({ type: 'date', name: 'trip_date' })
  tour_date: Date;

  @Column({ type: 'uuid', nullable: true })
  vehicle_id: string | null;

  @Column({ type: 'uuid', nullable: true })
  driver_id: string | null;

  /** Depot ZIP code (departure/return address). */
  @Column({ type: 'char', length: 5, nullable: true })
  depot_zip: string | null;

  /** Total driven distance in km — maps to legacy total_km column. */
  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true, name: 'total_km' })
  distance_km: number | null;

  /** Total tour duration in hours (driving + stops) — derived from total_drive_min. */
  @Column({ type: 'decimal', precision: 6, scale: 2, nullable: true })
  duration_hours: number | null;

  @Column({ type: 'int', nullable: true })
  stop_count: number | null;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  total_weight_kg: number | null;

  /** Full original row preserved from dispatcher CSV import. */
  @Column({ type: 'jsonb', nullable: true })
  raw_data: Record<string, unknown> | null;

  @CreateDateColumn()
  created_at: Date;

  @ManyToOne(() => FleetVehicle, { nullable: true })
  @JoinColumn({ name: 'vehicle_id' })
  vehicle: FleetVehicle | null;

  @ManyToOne(() => FleetDriver, { nullable: true })
  @JoinColumn({ name: 'driver_id' })
  driver: FleetDriver | null;

  @OneToMany(() => OwnTourStop, (stop) => stop.tour)
  stops: OwnTourStop[];
}
