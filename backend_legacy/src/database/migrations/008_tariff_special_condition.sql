-- Migration 008: tariff_special_condition table (Sonderkonditionen / Vereinbarungspreise)
-- Architecture §4.2 / §5.1 — closes GitHub issue #15
--
-- Sonderkonditionen override the standard tariff matrix and must be checked
-- BEFORE the standard zone × weight lookup in the benchmark engine.
--
-- Vereinbarungspreise (e.g. LA billing_type=200, EUR 530 flat for PLZ 61118)
-- are stored as condition_type = 'flat_tour' with dest_zip_prefix scope.

-- ─────────────────── UP ───────────────────────────────────────────────────

CREATE TABLE tariff_special_condition (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_id       uuid NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL,
  -- Type of override:
  --   fixed_price  : exact price regardless of zone/weight
  --   price_cap    : maximum allowed charge
  --   min_price    : minimum charge
  --   pct_discount : percentage discount off the standard rate
  --   flat_tour    : flat rate for a full tour (Vereinbarungspreis)
  condition_type  text NOT NULL
    CONSTRAINT tariff_special_condition_type_check
    CHECK (condition_type IN ('fixed_price', 'price_cap', 'min_price', 'pct_discount', 'flat_tour')),
  -- Scope — which shipments this condition applies to (all NULL = applies to all)
  dest_zip_prefix text,       -- e.g. '61118' (exact) or '61' (prefix match)
  weight_from_kg  numeric(10,2),
  weight_to_kg    numeric(10,2),
  -- The override value: EUR for fixed/cap/min/flat_tour, percentage for pct_discount
  value           numeric(12,4) NOT NULL,
  description     text,       -- e.g. 'Sonderpreis Kunde Mecu Zone 8'
  valid_from      date NOT NULL,
  valid_until     date
);

ALTER TABLE tariff_special_condition ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_special_condition
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_tariff_special_condition_tariff  ON tariff_special_condition(tariff_id);
CREATE INDEX idx_tariff_special_condition_tenant  ON tariff_special_condition(tenant_id);
-- Fast lookup during benchmark: find conditions for a given dest_zip and validity window
CREATE INDEX idx_tariff_special_condition_zip     ON tariff_special_condition(dest_zip_prefix);
CREATE INDEX idx_tariff_special_condition_valid   ON tariff_special_condition(valid_from, valid_until);


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- DROP TABLE tariff_special_condition;
