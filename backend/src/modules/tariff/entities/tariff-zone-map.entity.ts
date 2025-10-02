import { Entity, Column, PrimaryGeneratedColumn } from 'typeorm';

@Entity('tariff_zone_map')
export class TariffZoneMap {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  carrier_id: string;

  @Column({ length: 2 })
  country: string;

  @Column({ length: 5 })
  plz_prefix: string;

  @Column({ type: 'integer', nullable: true })
  prefix_len: number;

  @Column({ type: 'text', nullable: true })
  pattern: string;

  @Column({ type: 'integer' })
  zone: number;

  @Column({ type: 'date' })
  valid_from: Date;

  @Column({ type: 'date', nullable: true })
  valid_until: Date;
}
