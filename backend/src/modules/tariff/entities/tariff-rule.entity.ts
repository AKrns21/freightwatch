import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
} from 'typeorm';

@Entity('tariff_rule')
export class TariffRule {
  @PrimaryGeneratedColumn('uuid')
  id!: string;

  @Column('uuid')
  tenant_id!: string;

  @Column('uuid')
  carrier_id!: string;

  @Column({ length: 50 })
  rule_type!: string;

  @Column({ type: 'jsonb' })
  param_json!: any;

  @Column({ type: 'date' })
  valid_from!: Date;

  @Column({ type: 'date', nullable: true })
  valid_until!: Date | null;
}