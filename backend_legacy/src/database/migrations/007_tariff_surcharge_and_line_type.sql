-- Migration 007: tariff_surcharge table + invoice_line.line_type + carrier.billing_type_map
-- Architecture §4.2 / §5.1 / §12 — closes GitHub issue #14

-- ─────────────────── UP ───────────────────────────────────────────────────

-- 1. tariff_surcharge — flexible catch-all for non-standard surcharges.
--    Additive to tariff_nebenkosten (keep both). tariff_nebenkosten handles
--    known, typed Nebenkosten fields; this table catches anything else.
CREATE TABLE tariff_surcharge (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_id       uuid NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL,
  -- Type of surcharge, e.g. 'diesel_floater' | 'avis' | 'manual_order' | 'pallet_exchange'
  surcharge_type  text NOT NULL,
  -- How the surcharge is calculated: 'per_shipment' | 'pct_of_base' | 'flat'
  basis           text,
  value           numeric(12,4),
  currency        char(3) DEFAULT 'EUR',
  notes           text
);

ALTER TABLE tariff_surcharge ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_surcharge
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_tariff_surcharge_tariff ON tariff_surcharge(tariff_id);
CREATE INDEX idx_tariff_surcharge_tenant ON tariff_surcharge(tenant_id);


-- 2. invoice_line.line_type — classifies each line for benchmark routing.
--    standard    : Weight-based LTL/Stückgut charge — full tariff benchmark
--    vereinbarung: Flat tour/agreement price (e.g. LA 200) — matched against tariff_special_condition
--    surcharge   : Diesel, toll, Avis, pallet exchange, etc. — matched against tariff_nebenkosten/tariff_surcharge
--    one_time    : Unstructured one-off positions — stored as-is, total cost only
ALTER TABLE invoice_line
  ADD COLUMN line_type text
    CONSTRAINT invoice_line_type_check
    CHECK (line_type IN ('standard', 'vereinbarung', 'surcharge', 'one_time'));

CREATE INDEX idx_invoice_line_type ON invoice_line(line_type);


-- 3. carrier.billing_type_map — maps carrier-specific billing codes → line_type.
--    Example: { "200": "vereinbarung", "201": "standard", "900": "surcharge" }
--    Used to classify invoice lines when billing_type code is present.
--    LLM fallback is used for unknown codes.
ALTER TABLE carrier
  ADD COLUMN billing_type_map jsonb DEFAULT '{}'::jsonb;


-- ─────────────────── DOWN (rollback) ──────────────────────────────────────

-- To revert:
--
-- ALTER TABLE carrier DROP COLUMN billing_type_map;
-- ALTER TABLE invoice_line DROP COLUMN line_type;
-- DROP TABLE tariff_surcharge;
