import {
  Entity,
  Column,
  PrimaryColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { ServiceCatalog } from './service-catalog.entity';

@Entity('service_alias')
export class ServiceAlias {
  @PrimaryColumn('uuid', { nullable: true })
  tenant_id: string;

  @PrimaryColumn('uuid', { nullable: true })
  carrier_id: string;

  @PrimaryColumn({ length: 100 })
  alias_text: string;

  @Column({ length: 50 })
  service_code: string;

  @ManyToOne(() => ServiceCatalog)
  @JoinColumn({ name: 'service_code', referencedColumnName: 'code' })
  service: ServiceCatalog;
}