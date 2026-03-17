-- ============================================================================
-- Migration 005: Add missing columns to upload table
-- Date: 2026-03-17
--
-- The upload entity (upload.entity.ts) references columns that were not
-- included in the initial 001_fresh_schema.sql. This migration adds them.
-- ============================================================================

ALTER TABLE upload
  ADD COLUMN IF NOT EXISTS raw_text_hash     VARCHAR(64),
  ADD COLUMN IF NOT EXISTS suggested_mappings JSONB,
  ADD COLUMN IF NOT EXISTS reviewed_by        UUID,
  ADD COLUMN IF NOT EXISTS reviewed_at        TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS parsing_issues     JSONB,
  ADD COLUMN IF NOT EXISTS meta               JSONB,
  ADD COLUMN IF NOT EXISTS created_at         TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ DEFAULT now();

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_upload_updated_at
  BEFORE UPDATE ON upload
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();
