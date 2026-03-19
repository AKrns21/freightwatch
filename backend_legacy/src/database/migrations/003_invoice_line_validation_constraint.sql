-- Migration 003: DB constraint for invoice line minimum required fields
-- Ensures lines with neither weight nor location data cannot be saved silently

ALTER TABLE invoice_line
  ADD CONSTRAINT invoice_line_min_fields_check CHECK (
    weight_kg IS NOT NULL OR origin_zip IS NOT NULL OR dest_zip IS NOT NULL
  );
