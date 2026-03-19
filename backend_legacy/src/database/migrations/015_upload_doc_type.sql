-- Migration 015: Add doc_type column to upload table
-- Stores the auto-detected or user-overridden document type
-- from the document classification pipeline (issue #22)

ALTER TABLE upload
  ADD COLUMN IF NOT EXISTS doc_type VARCHAR(50);

COMMENT ON COLUMN upload.doc_type IS
  'Detected document type: tariff | invoice | shipment_csv | other';

CREATE INDEX IF NOT EXISTS idx_upload_doc_type ON upload (doc_type);
