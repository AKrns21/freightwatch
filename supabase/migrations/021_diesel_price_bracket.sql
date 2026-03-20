-- Migration 021: diesel price bracket table + destatis price cache
-- Replaces flat diesel_floater lookup with bracket-based resolution.
-- The diesel_floater table remains for manually overridden monthly rates;
-- bracket resolution takes precedence when a bracket table exists for the carrier.

-- Carrier-specific lookup table: diesel price (ct/liter) → surcharge %
CREATE TABLE IF NOT EXISTS diesel_price_bracket (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenant(id),
    carrier_id      UUID NOT NULL REFERENCES carrier(id),
    price_ct_max    NUMERIC(7, 2) NOT NULL,   -- upper bound in ct/liter (inclusive)
    floater_pct     NUMERIC(5, 2) NOT NULL,   -- surcharge % at this price level
    basis           VARCHAR(20) NOT NULL DEFAULT 'base',
    valid_from      DATE NOT NULL DEFAULT '2000-01-01',
    valid_until     DATE,
    UNIQUE (tenant_id, carrier_id, price_ct_max, valid_from)
);

-- Monthly diesel reference price cache (from Destatis GENESIS)
CREATE TABLE IF NOT EXISTS destatis_diesel_price (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    price_year  SMALLINT NOT NULL,
    price_month SMALLINT NOT NULL,   -- 1–12
    price_ct    NUMERIC(7, 2) NOT NULL,  -- ct/liter
    series_code VARCHAR(50) NOT NULL DEFAULT '61243-0001',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (price_year, price_month, series_code)
);

-- RLS for diesel_price_bracket (tenant-scoped)
ALTER TABLE diesel_price_bracket ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON diesel_price_bracket
    USING (tenant_id = current_setting('app.current_tenant')::uuid);

-- destatis_diesel_price is global (no tenant scope — same price for everyone)
-- No RLS needed.

-- Indexes
CREATE INDEX IF NOT EXISTS idx_diesel_price_bracket_carrier
    ON diesel_price_bracket (tenant_id, carrier_id, valid_from DESC);

CREATE INDEX IF NOT EXISTS idx_destatis_diesel_price_month
    ON destatis_diesel_price (price_year, price_month);
