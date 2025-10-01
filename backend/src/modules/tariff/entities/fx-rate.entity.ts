import {
  Entity,
  Column,
  PrimaryColumn,
} from 'typeorm';

@Entity('fx_rate')
export class FxRate {
  @PrimaryColumn({ type: 'date' })
  rate_date: Date;

  @PrimaryColumn({ type: 'char', length: 3 })
  from_ccy: string;

  @PrimaryColumn({ type: 'char', length: 3 })
  to_ccy: string;

  @Column({ type: 'numeric', precision: 18, scale: 8 })
  rate: number;

  @Column({ type: 'text', nullable: true })
  source: string;
}