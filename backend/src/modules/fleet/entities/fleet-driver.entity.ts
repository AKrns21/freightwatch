import { Entity, Column, PrimaryGeneratedColumn } from 'typeorm';

/**
 * FleetDriver — driver hourly rate for own-tour cost model (§5.2).
 *
 * Cost model contribution:
 *   driver_cost = own_tour.duration_hours × hourly_rate
 */
@Entity('fleet_driver')
export class FleetDriver {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column({ type: 'text' })
  name: string;

  /** Combined EUR/hour rate (driving + idle, for simplicity). */
  @Column({ type: 'decimal', precision: 8, scale: 2, nullable: true })
  hourly_rate: number | null;

  @Column({ type: 'char', length: 3, default: 'EUR' })
  currency: string;

  @Column({ type: 'boolean', default: true })
  active: boolean;
}
