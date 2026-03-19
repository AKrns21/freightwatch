-- Migration 011: invoice dispute workflow (invoice_dispute_event + invoice_line columns)
-- Architecture §5.5 — closes GitHub issue #18
--
-- Dispute state machine on invoice_line.dispute_status:
--   null → flagged → disputed → accepted | rejected → resolved | closed
--
-- invoice_dispute_event is the audit trail — every state transition creates
-- a new row. The current state is always the most recent event_type.

-- ─────────────────── UP ───────────────────────────────────────────────────

-- 1. Add dispute columns to invoice_line
ALTER TABLE invoice_line
  ADD COLUMN dispute_status text
    CONSTRAINT invoice_line_dispute_status_check
    CHECK (dispute_status IN ('flagged', 'disputed', 'accepted', 'rejected', 'resolved', 'closed')),
  ADD COLUMN dispute_note text;

CREATE INDEX idx_invoice_line_dispute ON invoice_line(dispute_status)
  WHERE dispute_status IS NOT NULL;


-- 2. invoice_dispute_event — one row per state transition (immutable audit trail)
CREATE TABLE invoice_dispute_event (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL,
  invoice_line_id   uuid NOT NULL REFERENCES invoice_line(id),
  -- The transition that occurred
  event_type        text NOT NULL
    CONSTRAINT invoice_dispute_event_type_check
    CHECK (event_type IN ('flagged', 'disputed', 'accepted', 'rejected', 'resolved', 'closed')),
  -- EUR amount the consultant believes was overcharged (set when raising dispute)
  amount_claimed    numeric(12,2),
  -- EUR amount actually recovered (set on 'resolved')
  amount_recovered  numeric(12,2),
  note              text,
  created_by        uuid,   -- user_id of the consultant who triggered this transition
  created_at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE invoice_dispute_event ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invoice_dispute_event
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_invoice_dispute_event_line   ON invoice_dispute_event(invoice_line_id);
CREATE INDEX idx_invoice_dispute_event_tenant ON invoice_dispute_event(tenant_id);
-- Efficient lookup of latest event per line (for dispute summary report)
CREATE INDEX idx_invoice_dispute_event_recent ON invoice_dispute_event(invoice_line_id, created_at DESC);


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- DROP TABLE invoice_dispute_event;
-- ALTER TABLE invoice_line DROP COLUMN dispute_note;
-- ALTER TABLE invoice_line DROP COLUMN dispute_status;
