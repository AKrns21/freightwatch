-- Migration 010: raw_extraction table + international lane columns + GoBD retention
-- Architecture §5.4 / §7 / §7.1 — closes GitHub issue #17
--
-- Three additions:
--   1. raw_extraction — data lake audit trail for all parsed documents
--   2. tariff_table.dest_country_codes — international lane routing (§5.4)
--   3. tenant.data_retention_years — GoBD 10-year retention (§7.1)
--
-- GoBD §14b: raw_extraction must NEVER be deleted within the retention window.
-- retain_until is computed at INSERT time: extracted_at::date + data_retention_years years.
-- A nightly archival job flags rows where retain_until < CURRENT_DATE as eligible
-- for deletion — but requires human sign-off; no automated deletion.

-- ─────────────────── UP ───────────────────────────────────────────────────

-- 1. raw_extraction — stores raw LLM/parser output before normalization.
--    Enables re-processing if parsing logic improves and satisfies GoBD audit.
CREATE TABLE raw_extraction (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  upload_id       uuid NOT NULL REFERENCES upload(id),
  -- Document type at time of extraction
  doc_type        text NOT NULL,   -- 'invoice' | 'tariff' | 'shipment_csv' | 'tour'
  extracted_at    timestamptz NOT NULL DEFAULT now(),
  -- Which parser/model produced this extraction
  extractor       text NOT NULL,   -- e.g. 'claude-vision' | 'csv-parser' | 'template:carrier_xyz_v2'
  -- Document-level confidence (0.000–1.000); NULL if not applicable (e.g. CSV)
  confidence      numeric(4,3),
  -- Full raw payload as output by the LLM or parser, before any normalization
  payload         jsonb NOT NULL,
  -- Warnings and parse errors from the extraction step
  issues          text[],
  -- Normalization state
  normalized      boolean NOT NULL DEFAULT false,
  normalized_at   timestamptz,
  -- GoBD retention: set at insert, never updated. retain_until = extracted_at::date + data_retention_years
  retain_until    date NOT NULL
);

ALTER TABLE raw_extraction ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON raw_extraction
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_raw_extraction_upload    ON raw_extraction(upload_id);
CREATE INDEX idx_raw_extraction_tenant    ON raw_extraction(tenant_id);
CREATE INDEX idx_raw_extraction_normalized ON raw_extraction(normalized) WHERE normalized = false;
-- Index for the nightly archival job
CREATE INDEX idx_raw_extraction_retain    ON raw_extraction(retain_until);


-- 2. tariff_table.dest_country_codes — enables international lane routing.
--    Domestic tariff: ['DE']
--    DE→AT tariff:    ['AT']
--    DE→UK tariff:    ['GB']
--    NULL = unspecified (treated as domestic DE for backwards compat)
ALTER TABLE tariff_table
  ADD COLUMN dest_country_codes text[];

CREATE INDEX idx_tariff_table_dest_countries ON tariff_table USING GIN (dest_country_codes);


-- 3. tenant.data_retention_years — GoBD retention period per tenant.
--    Default: 10 years (GoBD §14b minimum for invoices in DE).
--    Used to compute raw_extraction.retain_until at insert time.
ALTER TABLE tenant
  ADD COLUMN data_retention_years int NOT NULL DEFAULT 10;


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- ALTER TABLE tenant DROP COLUMN data_retention_years;
-- ALTER TABLE tariff_table DROP COLUMN dest_country_codes;
-- DROP TABLE raw_extraction;
