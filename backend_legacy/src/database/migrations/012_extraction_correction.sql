-- Migration 012: extraction_correction table (human OCR correction audit trail)
-- Architecture §3.2 / §4.2 — closes GitHub issue #19
--
-- Stores every field-level correction a consultant makes in the review UI.
-- Used for:
--   - Audit trail: proves what was changed and why (GoBD)
--   - Future prompt improvement: feed corrections back into extraction prompts
--   - Re-normalization: corrections trigger re-processing of raw_extraction payload

-- ─────────────────── UP ───────────────────────────────────────────────────

CREATE TABLE extraction_correction (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  upload_id       uuid NOT NULL REFERENCES upload(id),
  -- JSON path to the corrected field, e.g. "lines[3].weight_kg" or "header.invoice_date"
  field_path      text NOT NULL,
  -- Value as originally extracted by LLM/parser (NULL if field was missing)
  original_value  text,
  -- Value as corrected by the consultant
  corrected_value text NOT NULL,
  corrected_by    uuid,   -- user_id of the consultant
  corrected_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE extraction_correction ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON extraction_correction
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_extraction_correction_upload  ON extraction_correction(upload_id);
CREATE INDEX idx_extraction_correction_tenant  ON extraction_correction(tenant_id);


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- DROP TABLE extraction_correction;
