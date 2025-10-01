import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
} from 'typeorm';

@Entity('diesel_floater')
export class DieselFloater {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  carrier_id: string;

  @Column({ type: 'decimal', precision: 5, scale: 2 })
  floater_pct: number;

  @Column({ length: 20, default: 'base' })
  basis: string;

  @Column({ type: 'date' })
  valid_from: Date;

  @Column({ type: 'date', nullable: true })
  valid_until: Date | null;
}