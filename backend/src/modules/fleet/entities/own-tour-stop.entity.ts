import { Entity, Column, PrimaryGeneratedColumn, ManyToOne, JoinColumn } from 'typeorm';
import { OwnTour } from './own-tour.entity';

/**
 * OwnTourStop — one delivery stop within an OwnTour.
 *
 * shipment_ref is a soft FK to shipment.shipment_reference.
 * It enables the benchmark engine to look up the matching carrier invoice
 * line for each stop, making the own vs. carrier cost comparison possible.
 *
 * Legacy telemetry columns (departure_address, arrival_locality, drive_min,
 * idle_before_min, idle_after_min, distance_km, is_delivery, meta) are
 * preserved in the DB from the original route_stop definition.
 */
@Entity('own_tour_stop')
export class OwnTourStop {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  tour_id: string;

  @Column({ type: 'int' })
  stop_sequence: number;

  /** Destination ZIP — maps to legacy arrival_zip column. */
  @Column({ type: 'varchar', length: 10, nullable: true, name: 'arrival_zip' })
  dest_zip: string | null;

  /** Destination city/locality — maps to legacy arrival_locality column. */
  @Column({ type: 'varchar', length: 100, nullable: true, name: 'arrival_locality' })
  dest_city: string | null;

  /**
   * Soft FK to shipment.shipment_reference.
   * Links this stop to an invoice line for carrier cost lookup.
   */
  @Column({ type: 'text', nullable: true })
  shipment_ref: string | null;

  @Column({ type: 'decimal', precision: 10, scale: 2, nullable: true })
  weight_kg: number | null;

  @Column({ type: 'int', nullable: true })
  packages: number | null;

  @Column({ type: 'timestamptz', nullable: true, name: 'arrival_at' })
  arrival_time: Date | null;

  @Column({ type: 'timestamptz', nullable: true, name: 'departure_at' })
  departure_time: Date | null;

  @ManyToOne(() => OwnTour, (tour) => tour.stops)
  @JoinColumn({ name: 'tour_id' })
  tour: OwnTour;
}
