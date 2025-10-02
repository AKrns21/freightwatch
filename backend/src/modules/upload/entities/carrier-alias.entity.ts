import { Entity, Column, PrimaryGeneratedColumn, Index } from 'typeorm';

@Entity('carrier_alias')
@Index(['tenant_id', 'alias_text'], { unique: true })
export class CarrierAlias {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid', { nullable: true })
  tenant_id: string | null;

  @Column({ length: 255 })
  alias_text: string;

  @Column('uuid')
  carrier_id: string;
}
