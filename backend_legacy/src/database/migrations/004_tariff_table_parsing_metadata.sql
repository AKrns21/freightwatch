-- Migration 004: Add parsing metadata columns to tariff_table
-- Enables traceability of how each tariff was extracted (template vs LLM)

ALTER TABLE tariff_table
  ADD COLUMN IF NOT EXISTS confidence  DECIMAL(3,2)  DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS source_data JSONB         DEFAULT NULL;

COMMENT ON COLUMN tariff_table.confidence  IS 'Parsing confidence score (0.00–1.00)';
COMMENT ON COLUMN tariff_table.source_data IS 'Parsing metadata: parsing_method, parsing_issues[]';
