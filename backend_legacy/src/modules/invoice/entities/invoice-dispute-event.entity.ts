import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  ManyToOne,
  JoinColumn,
  CreateDateColumn,
} from 'typeorm';
import { InvoiceLine } from './invoice-line.entity';

export type DisputeStatus =
  | 'flagged'
  | 'disputed'
  | 'accepted'
  | 'rejected'
  | 'resolved'
  | 'closed';

/**
 * InvoiceDisputeEvent — immutable audit trail for carrier dispute transitions.
 *
 * State machine on invoice_line.dispute_status:
 *   null → flagged → disputed → accepted | rejected → resolved | closed
 *
 * Every state transition creates a new row here. The current dispute state
 * is always the event_type of the most recent row for a given invoice_line_id.
 *
 * Reporting dimension (dispute summary per project):
 *   Total overcharge identified : shipment_benchmark WHERE status = 'drüber'
 *   Disputed with carriers      : dispute_status IN ('disputed', 'accepted')
 *   Already recovered           : dispute_status = 'resolved' → SUM(amount_recovered)
 *   Written off                 : dispute_status = 'closed'
 *   Not yet actioned            : status = 'drüber' AND dispute_status IS NULL
 */
@Entity('invoice_dispute_event')
export class InvoiceDisputeEvent {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('uuid')
  tenant_id: string;

  @Column('uuid')
  invoice_line_id: string;

  /**
   * The dispute state transition that occurred.
   * flagged   : Consultant flagged internally — not yet raised with carrier
   * disputed  : Formally raised with carrier
   * accepted  : Carrier acknowledged overpayment — credit note expected
   * rejected  : Carrier disputes the claim
   * resolved  : Credit note received or agreed settlement booked
   * closed    : Written off without recovery
   */
  @Column({ type: 'text' })
  event_type: DisputeStatus;

  /** EUR amount the consultant believes was overcharged. Set when raising dispute. */
  @Column({ type: 'decimal', precision: 12, scale: 2, nullable: true })
  amount_claimed: number | null;

  /** EUR amount actually recovered. Set on 'resolved'. */
  @Column({ type: 'decimal', precision: 12, scale: 2, nullable: true })
  amount_recovered: number | null;

  @Column({ type: 'text', nullable: true })
  note: string | null;

  /** User ID of the consultant who triggered this transition. */
  @Column({ type: 'uuid', nullable: true })
  created_by: string | null;

  @CreateDateColumn()
  created_at: Date;

  @ManyToOne(() => InvoiceLine)
  @JoinColumn({ name: 'invoice_line_id' })
  invoice_line: InvoiceLine;
}
