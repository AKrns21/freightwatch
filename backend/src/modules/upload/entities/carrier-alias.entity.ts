import {
  Entity,
  Column,
  PrimaryColumn,
} from 'typeorm';

@Entity('carrier_alias')
export class CarrierAlias {
  @PrimaryColumn('uuid', { nullable: true })
  tenant_id!: string | null;

  @PrimaryColumn({ length: 255 })
  alias_text!: string;

  @Column('uuid')
  carrier_id!: string;
}