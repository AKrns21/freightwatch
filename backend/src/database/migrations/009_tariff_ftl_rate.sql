-- Migration 009: tariff_ftl_rate table (FTL/Charter rates)
-- Architecture §2.1 / §4.2 — closes GitHub issue #16
--
-- FTL/Charter shipments use flat per-km, per-day, or flat-tour rates —
-- not the zone × weight matrix used for LTL (Stückgut).
-- The benchmark engine branches on invoice_line service_type to select
-- this table instead of tariff_rate.
--
-- Rate calculation by rate_basis:
--   per_km     : price × distance_km  (apply min_price if set)
--   per_day    : price × days          (apply min_price if set)
--   flat_tour  : price as-is           (apply min_price if set)

-- ─────────────────── UP ───────────────────────────────────────────────────

CREATE TABLE tariff_ftl_rate (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_id       uuid NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL,
  -- How the rate is applied
  rate_basis      text NOT NULL
    CONSTRAINT tariff_ftl_rate_basis_check
    CHECK (rate_basis IN ('per_km', 'per_day', 'flat_tour')),
  -- Optional scope filters (NULL = matches any)
  vehicle_type    text,   -- e.g. '7.5t' | '12t' | '40t'
  dest_region     text,   -- optional PLZ prefix or free-text region label
  price           numeric(12,4) NOT NULL,
  currency        char(3) DEFAULT 'EUR',
  -- Minimum charge per tour (guards against very short trips on per_km rates)
  min_price       numeric(12,4),
  notes           text
);

ALTER TABLE tariff_ftl_rate ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_ftl_rate
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_tariff_ftl_rate_tariff  ON tariff_ftl_rate(tariff_id);
CREATE INDEX idx_tariff_ftl_rate_tenant  ON tariff_ftl_rate(tenant_id);


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- DROP TABLE tariff_ftl_rate;
