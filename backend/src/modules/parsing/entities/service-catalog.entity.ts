import {
  Entity,
  Column,
  PrimaryColumn,
} from 'typeorm';

@Entity('service_catalog')
export class ServiceCatalog {
  @PrimaryColumn({ length: 50 })
  code: string;

  @Column({ type: 'text', nullable: true })
  description: string;

  @Column({ length: 50, nullable: true })
  category: string;
}