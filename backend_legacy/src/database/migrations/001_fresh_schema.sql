-- ============================================================================
-- FreightWatch Fresh Schema for Supabase
-- Version: 2.0
-- Date: 2026-03-16
--
-- Single clean migration for a fresh Supabase instance.
-- Covers all three data sources:
--   1. Tariff sheets  (Tarifblätter)
--   2. Invoices       (Spediteursrechnungen)
--   3. Fleet/Route    (Eigener Fuhrpark / Telematik)
--
-- Design principles:
--   - RLS on every tenant-scoped table
--   - Normalized tariff model (header + rates, not flat)
--   - Invoice lines rich enough for AS Stahl LA 200/201 pattern
--   - Route model with trip → stop hierarchy
--   - Nebenkosten as structured table, not buried in JSONB
--   - All monetary columns: NUMERIC(12,2), weights: NUMERIC(10,2)
-- ============================================================================

-- Required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "btree_gist";


-- ============================================================================
-- 1. CORE / STAMMDATEN
-- ============================================================================

-- 1a. Tenant ----------------------------------------------------------------

CREATE TABLE tenant (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          VARCHAR(255) NOT NULL,
  settings      JSONB DEFAULT '{
    "currency": "EUR",
    "country": "DE",
    "default_diesel_pct": 18.5,
    "data_retention_days": 2555
  }'::jsonb,
  created_at    TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE tenant ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant
  USING (id = current_setting('app.current_tenant', true)::UUID);


-- 1b. Carrier ---------------------------------------------------------------

CREATE TABLE carrier (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          VARCHAR(255) NOT NULL,
  code_norm     VARCHAR(50) UNIQUE NOT NULL,   -- 'COSI', 'AS_STAHL'
  country       VARCHAR(2),
  -- Carrier-level conversion rules (LDM factor, min pallet weight, etc.)
  -- These are global defaults; customer-specific overrides go in tariff_nebenkosten.
  conversion_rules JSONB DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- Alias table so "AS Stahl und Logistik GmbH & Co. KG" → carrier.AS_STAHL
CREATE TABLE carrier_alias (
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  alias_text    VARCHAR(255) NOT NULL,
  carrier_id    UUID NOT NULL REFERENCES carrier(id),
  PRIMARY KEY (tenant_id, alias_text)
);


-- 1c. Upload ----------------------------------------------------------------

CREATE TABLE upload (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  project_id    UUID,  -- FK added after project table creation
  filename      VARCHAR(500) NOT NULL,
  file_hash     VARCHAR(64) NOT NULL,
  mime_type     VARCHAR(100),
  source_type   VARCHAR(50),  -- 'tariff', 'invoice', 'shipment_list', 'route_log'
  storage_url   TEXT,
  status        VARCHAR(50) DEFAULT 'pending',
  -- LLM/parsing metadata
  parse_method  VARCHAR(50),   -- 'xlsx_direct', 'pdf_vision', 'csv_direct', 'llm'
  confidence    NUMERIC(3,2),
  llm_analysis  JSONB,
  parse_errors  JSONB,
  -- Idempotency
  received_at   TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tenant_id, file_hash)
);

ALTER TABLE upload ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON upload
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);


-- 1d. FX Rates (no tenant scope — global reference data) --------------------

CREATE TABLE fx_rate (
  rate_date     DATE NOT NULL,
  from_ccy      CHAR(3) NOT NULL,
  to_ccy        CHAR(3) NOT NULL,
  rate          NUMERIC(18,8) NOT NULL,
  source        TEXT,   -- 'ecb', 'manual'
  PRIMARY KEY (rate_date, from_ccy, to_ccy)
);

CREATE INDEX idx_fx_lookup ON fx_rate(from_ccy, to_ccy, rate_date DESC);


-- ============================================================================
-- 2. PROJECT SYSTEM
-- ============================================================================

CREATE TABLE project (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  name          VARCHAR(255) NOT NULL,
  customer_name VARCHAR(255),          -- "Mecu Metallhalbzeug"
  phase         VARCHAR(50) DEFAULT 'quick_check',
  status        VARCHAR(50) DEFAULT 'draft',
  consultant_id UUID,
  metadata      JSONB DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now(),
  deleted_at    TIMESTAMPTZ
);

ALTER TABLE project ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_project_tenant ON project(tenant_id);

-- Now add the FK from upload → project
ALTER TABLE upload ADD CONSTRAINT fk_upload_project
  FOREIGN KEY (project_id) REFERENCES project(id);
CREATE INDEX idx_upload_project ON upload(project_id);


-- ============================================================================
-- 3. TARIFF SYSTEM (normalized: header → rates, separate zone map)
-- ============================================================================

-- 3a. Tariff Table (header) -------------------------------------------------
-- One row per "Tarifblatt": Cosi→Mecu AT, AS Stahl DE, etc.

CREATE TABLE tariff_table (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  carrier_id      UUID NOT NULL REFERENCES carrier(id),
  upload_id       UUID REFERENCES upload(id),     -- source document
  name            VARCHAR(255),                    -- 'Stückgutversand Österreich'
  service_type    VARCHAR(100),                    -- from tariff sheet title
  lane_type       VARCHAR(20) NOT NULL,            -- 'DE', 'AT', 'CH', 'EU', 'EXPORT'
  tariff_country  CHAR(2),                         -- destination country ISO
  currency        CHAR(3) NOT NULL DEFAULT 'EUR',
  valid_from      DATE NOT NULL,
  valid_until     DATE,
  origin_info     VARCHAR(255),                    -- 'ab Velbert' / 'ab Hagen'
  delivery_condition VARCHAR(50),                  -- 'frei Haus', 'ab Werk'
  maut_included   BOOLEAN DEFAULT FALSE,
  notes           TEXT,
  source_data     JSONB,                           -- raw extraction JSON
  created_at      TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE tariff_table ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_table
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_tariff_table_lookup ON tariff_table(tenant_id, carrier_id, lane_type);


-- 3b. Tariff Rate (price matrix rows) --------------------------------------
-- One row per zone × weight band combination.

CREATE TABLE tariff_rate (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_table_id UUID NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  zone            INTEGER NOT NULL,
  weight_from_kg  NUMERIC(10,2) NOT NULL,
  weight_to_kg    NUMERIC(10,2) NOT NULL,
  -- Pricing: one of these is set
  rate_per_shipment NUMERIC(12,2),    -- flat rate per shipment in weight band
  rate_per_kg       NUMERIC(12,4),    -- per-kg rate (rare but exists)
  -- Constraints
  CONSTRAINT chk_rate_has_price CHECK (
    rate_per_shipment IS NOT NULL OR rate_per_kg IS NOT NULL
  )
);

CREATE INDEX idx_tariff_rate_lookup ON tariff_rate(tariff_table_id, zone, weight_from_kg, weight_to_kg);


-- 3c. Tariff Zone Map -------------------------------------------------------
-- Maps PLZ prefixes → zone numbers. Carrier + customer specific.

CREATE TABLE tariff_zone_map (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_table_id UUID NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  country_code  CHAR(2) NOT NULL,        -- 'DE', 'AT', 'CH'
  plz_prefix    VARCHAR(10) NOT NULL,    -- '42', '8', '1010' etc.
  match_type    VARCHAR(10) NOT NULL DEFAULT 'prefix',  -- 'prefix' or 'exact'
  zone          INTEGER NOT NULL
);

CREATE INDEX idx_zone_map_lookup ON tariff_zone_map(tariff_table_id, country_code, plz_prefix);


-- 3d. Tariff Nebenkosten (surcharges & conditions) --------------------------
-- Structured storage for the conditions block on tariff sheets.
-- Scoped to a specific tariff_table (= carrier + customer + validity).

CREATE TABLE tariff_nebenkosten (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_table_id   UUID NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,

  -- Diesel
  diesel_floater_pct          NUMERIC(5,2),
  eu_mobility_surcharge_pct   NUMERIC(5,2),    -- "akt. 5 Prozent"

  -- Weight minimums (chargeable weight rules)
  min_weight_pallet_kg        NUMERIC(10,2),   -- pro Palette
  min_weight_cbm_kg           NUMERIC(10,2),   -- pro Kubikmeter
  min_weight_ldm_kg           NUMERIC(10,2),   -- pro Lademeter
  min_weight_small_format_kg  NUMERIC(10,2),   -- Kleinformat
  min_weight_medium_format_kg NUMERIC(10,2),   -- Mittelformat
  min_weight_large_format_kg  NUMERIC(10,2),   -- Großformat

  -- Pallet exchange
  pallet_exchange_euro_flat   NUMERIC(10,2),
  pallet_exchange_euro_mesh   NUMERIC(10,2),
  pallet_exchange_note        TEXT,             -- 'kein Tausch möglich!'

  -- Miscellaneous
  return_pickup_note          TEXT,             -- 'nach Absprache'
  transport_insurance          TEXT,             -- 'Verzichtskunde'
  hazmat_surcharge            TEXT,             -- Gefahrgut
  liability_surcharge         TEXT,             -- Haftungszuschlag
  oversize_note               TEXT,             -- 'ab 2001 kg Tagespreise'
  island_trade_fair_surcharge TEXT,
  legal_basis                 TEXT,             -- 'ADSp 2017'

  -- Payment
  payment_terms               TEXT,
  payment_days                INTEGER,

  -- Catch-all for anything we haven't typed yet
  raw_items                   JSONB             -- [{label, value}, ...]
);


-- 3e. Maut Tariff -----------------------------------------------------------
-- Separate table because Maut has distance-range-based zones, not PLZ zones.

CREATE TABLE maut_table (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_table_id UUID NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  label           VARCHAR(255),          -- 'Maut-Tarif bis 3000 kg'
  weight_from_kg  NUMERIC(10,2),         -- 1 for first table
  weight_limit_kg NUMERIC(10,2),         -- 3000
  minimum_charge  NUMERIC(12,2),
  currency        CHAR(3) DEFAULT 'EUR'
);

CREATE TABLE maut_rate (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  maut_table_id   UUID NOT NULL REFERENCES maut_table(id) ON DELETE CASCADE,
  weight_from_kg  NUMERIC(10,2) NOT NULL,
  weight_to_kg    NUMERIC(10,2) NOT NULL,
  distance_range  VARCHAR(30) NOT NULL,  -- '001-100 km', '101-200 km'
  rate            NUMERIC(12,2) NOT NULL
);

CREATE TABLE maut_zone_map (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  maut_table_id   UUID NOT NULL REFERENCES maut_table(id) ON DELETE CASCADE,
  country_code    CHAR(2) NOT NULL,
  plz_prefix      VARCHAR(10) NOT NULL,
  match_type      VARCHAR(10) DEFAULT 'prefix',
  distance_zone   VARCHAR(30) NOT NULL   -- maps to maut_rate.distance_range
);


-- 3f. LSVA Tariff (Swiss heavy vehicle charge) ------------------------------

CREATE TABLE lsva_table (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_table_id UUID NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  currency        CHAR(3) DEFAULT 'CHF',
  valid_from      DATE,
  weight_threshold_kg NUMERIC(10,2),     -- billing unit changes above this
  billing_unit_above  VARCHAR(50)        -- 'per 100 kg'
);

CREATE TABLE lsva_rate (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lsva_table_id   UUID NOT NULL REFERENCES lsva_table(id) ON DELETE CASCADE,
  zone            INTEGER NOT NULL,
  weight_from_kg  NUMERIC(10,2) NOT NULL,
  weight_to_kg    NUMERIC(10,2) NOT NULL,
  rate            NUMERIC(12,2) NOT NULL
);


-- 3g. City Surcharges (Großstadtzuschläge) ----------------------------------

CREATE TABLE city_surcharge (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_table_id UUID NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  city            VARCHAR(100) NOT NULL,
  country_code    CHAR(2) DEFAULT 'DE',
  plz_from        VARCHAR(10),
  plz_to          VARCHAR(10),
  surcharge_pct   NUMERIC(5,2),
  surcharge_flat  NUMERIC(12,2),
  note            TEXT
);


-- 3h. Diesel Floater (time-series, independent of tariff sheet) -------------
-- Tracks the actual diesel % over time per carrier.

CREATE TABLE diesel_floater (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  carrier_id    UUID NOT NULL REFERENCES carrier(id),
  valid_from    DATE NOT NULL,
  valid_until   DATE,
  floater_pct   NUMERIC(5,2) NOT NULL,
  basis         VARCHAR(20) DEFAULT 'base'
                CHECK (basis IN ('base', 'base_plus_toll', 'total')),
  source        VARCHAR(100),  -- 'tariff_sheet', 'email', 'manual'
  UNIQUE(tenant_id, carrier_id, valid_from)
);

ALTER TABLE diesel_floater ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON diesel_floater
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);


-- ============================================================================
-- 4. INVOICE SYSTEM
-- ============================================================================

-- 4a. Invoice Header --------------------------------------------------------

CREATE TABLE invoice_header (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  project_id      UUID REFERENCES project(id),
  upload_id       UUID REFERENCES upload(id),
  carrier_id      UUID REFERENCES carrier(id),

  invoice_number  VARCHAR(100) NOT NULL,     -- Beleg-Nr: 117261
  invoice_date    DATE NOT NULL,
  print_date      DATE,                      -- Druckdatum (often different)

  customer_name   VARCHAR(255),
  customer_number VARCHAR(50),               -- Kunden-Nr: 100066
  customer_vat_id VARCHAR(50),               -- USt-ID

  total_net       NUMERIC(12,2),
  total_tax       NUMERIC(12,2),
  total_gross     NUMERIC(12,2),
  currency        CHAR(3) DEFAULT 'EUR',
  tax_rate_pct    NUMERIC(5,2),              -- 19.00

  payment_terms   TEXT,                      -- 'Sofort rein netto'
  status          VARCHAR(50) DEFAULT 'pending',

  -- ERP cover-sheet data (the Navision/BC deckblatt)
  erp_document_number VARCHAR(100),          -- KRE-RG+096379
  erp_creditor_number VARCHAR(50),           -- K80215
  erp_barcode         VARCHAR(50),           -- 219297

  source_data     JSONB,
  meta            JSONB DEFAULT '{}'::jsonb,
  confidence      NUMERIC(3,2),
  parse_issues    TEXT[],

  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tenant_id, carrier_id, invoice_number, invoice_date)
);

ALTER TABLE invoice_header ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invoice_header
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_invoice_tenant_date ON invoice_header(tenant_id, invoice_date);
CREATE INDEX idx_invoice_carrier ON invoice_header(carrier_id);


-- 4b. Invoice Line ----------------------------------------------------------
-- One row per shipment/charge on the invoice.
-- Rich enough for AS Stahl's LA 200/201 pattern.

CREATE TABLE invoice_line (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  invoice_id      UUID NOT NULL REFERENCES invoice_header(id) ON DELETE CASCADE,

  line_number     INTEGER,
  page_number     INTEGER,                   -- which PDF page

  -- Billing classification
  la_code         VARCHAR(10),               -- '200' = Vereinbarung, '201' = Fracht lt. Tarif
  billing_type    VARCHAR(50),               -- 'vereinbarung', 'tarif', 'retoure', 'sonstig'
  billing_description TEXT,                  -- 'Fracht lt. Tarif', 'Fracht lt. Vereinbarung'

  -- Shipment identification
  auftragsnummer  VARCHAR(50),               -- 230300073
  tour_number     VARCHAR(50),               -- 24475
  referenz        TEXT,                      -- '445942, 446243, 446080' (can be multi)
  shipment_date   DATE,                      -- Leistungstag

  -- Route
  origin_address_raw  TEXT,                  -- 'Mecu Metallhalbzeug, D-42551 Velbert'
  origin_zip          VARCHAR(10),
  origin_country      CHAR(2) DEFAULT 'DE',
  dest_address_raw    TEXT,                  -- 'TBI Industries GmbH, D-35463 Fernwald'
  dest_zip            VARCHAR(10),
  dest_country        CHAR(2) DEFAULT 'DE',

  -- Quantities
  weight_kg       NUMERIC(10,2),             -- Menge in kg
  quantity        NUMERIC(10,2),             -- Menge (for LA 200: 1.00 Stück)
  unit            VARCHAR(20),               -- 'Kg', 'Stück'

  -- Amounts
  unit_price      NUMERIC(12,2),             -- Preis
  line_total      NUMERIC(12,2),             -- GesamtEUR
  currency        CHAR(3) DEFAULT 'EUR',

  -- Matching
  shipment_id     UUID,                      -- FK to shipment (added after shipment table)
  match_status    VARCHAR(20),               -- 'matched', 'unmatched', 'ambiguous', 'manual'
  match_confidence NUMERIC(3,2),

  source_data     JSONB,
  meta            JSONB DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE invoice_line ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invoice_line
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_invoice_line_invoice ON invoice_line(invoice_id);
CREATE INDEX idx_invoice_line_auftrag ON invoice_line(auftragsnummer);
CREATE INDEX idx_invoice_line_tour ON invoice_line(tour_number);
CREATE INDEX idx_invoice_line_date ON invoice_line(shipment_date);
CREATE INDEX idx_invoice_line_dest ON invoice_line(dest_zip);


-- ============================================================================
-- 5. SHIPMENT SYSTEM (the unified view of "what was shipped")
-- ============================================================================

CREATE TABLE shipment (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  project_id      UUID REFERENCES project(id),
  upload_id       UUID REFERENCES upload(id),
  carrier_id      UUID REFERENCES carrier(id),

  -- Identity
  date            DATE NOT NULL,
  reference_number VARCHAR(100),
  service_level   VARCHAR(20) DEFAULT 'STANDARD',

  -- Route
  origin_zip      VARCHAR(10),
  origin_country  CHAR(2) DEFAULT 'DE',
  dest_zip        VARCHAR(10),
  dest_country    CHAR(2) DEFAULT 'DE',

  -- Dimensions
  weight_kg       NUMERIC(10,2),
  volume_cbm      NUMERIC(10,3),
  pallets         NUMERIC(5,2),
  length_m        NUMERIC(5,2),
  pieces          INTEGER,

  -- Chargeable weight (calculated)
  chargeable_weight_kg NUMERIC(10,2),
  chargeable_basis     VARCHAR(20),   -- 'kg', 'lm', 'pallet', 'cbm', 'format'

  -- Actual costs (from invoice)
  currency        CHAR(3) DEFAULT 'EUR',
  actual_base_amount    NUMERIC(12,2),
  actual_diesel_amount  NUMERIC(12,2),
  actual_toll_amount    NUMERIC(12,2),
  actual_other_amount   NUMERIC(12,2),
  actual_total_amount   NUMERIC(12,2),

  -- Data quality
  completeness_score    NUMERIC(3,2),
  missing_fields        TEXT[],
  data_quality_issues   JSONB,

  -- Lineage
  source_data     JSONB,
  extraction_method VARCHAR(50),
  confidence_score NUMERIC(3,2),

  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now(),
  deleted_at      TIMESTAMPTZ
);

ALTER TABLE shipment ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shipment
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_shipment_tenant ON shipment(tenant_id);
CREATE INDEX idx_shipment_project ON shipment(project_id);
CREATE INDEX idx_shipment_carrier ON shipment(carrier_id);
CREATE INDEX idx_shipment_date ON shipment(date);
CREATE INDEX idx_shipment_dest ON shipment(dest_zip);
CREATE INDEX idx_shipment_active ON shipment(tenant_id) WHERE deleted_at IS NULL;

-- Now add the FK from invoice_line → shipment
ALTER TABLE invoice_line ADD CONSTRAINT fk_invoice_line_shipment
  FOREIGN KEY (shipment_id) REFERENCES shipment(id);
CREATE INDEX idx_invoice_line_shipment ON invoice_line(shipment_id);


-- ============================================================================
-- 6. BENCHMARKING SYSTEM
-- ============================================================================

CREATE TABLE shipment_benchmark (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shipment_id     UUID NOT NULL REFERENCES shipment(id),
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  tariff_table_id UUID REFERENCES tariff_table(id),

  -- Zone/weight used
  zone_calculated     INTEGER,
  chargeable_weight   NUMERIC(10,2),
  chargeable_basis    VARCHAR(20),

  -- Expected costs
  expected_base_amount    NUMERIC(12,2),
  expected_diesel_amount  NUMERIC(12,2),
  expected_toll_amount    NUMERIC(12,2),
  expected_total_amount   NUMERIC(12,2),

  -- Actuals (snapshot)
  actual_total_amount     NUMERIC(12,2),

  -- Delta
  delta_amount    NUMERIC(12,2),
  delta_pct       NUMERIC(6,2),
  classification  VARCHAR(20),       -- 'unter', 'im_markt', 'drüber'

  -- Calculation trace
  currency        CHAR(3) DEFAULT 'EUR',
  report_currency CHAR(3),
  fx_rate_used    NUMERIC(18,8),
  fx_rate_date    DATE,
  diesel_basis_used VARCHAR(20),
  diesel_pct_used   NUMERIC(5,2),
  cost_breakdown    JSONB,           -- itemized array
  report_amounts    JSONB,           -- converted to report currency
  calc_version      VARCHAR(20),
  calculation_metadata JSONB,

  created_at      TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE shipment_benchmark ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shipment_benchmark
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_benchmark_shipment ON shipment_benchmark(shipment_id);
CREATE INDEX idx_benchmark_class ON shipment_benchmark(tenant_id, classification);


-- ============================================================================
-- 7. FLEET / ROUTE SYSTEM (Eigener Fuhrpark)
-- ============================================================================

-- 7a. Vehicle ---------------------------------------------------------------

CREATE TABLE vehicle (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  vehicle_type  VARCHAR(100),        -- 'MAN TGL'
  plate_number  VARCHAR(20),         -- 'ME CU 167'
  active        BOOLEAN DEFAULT TRUE,
  meta          JSONB DEFAULT '{}'::jsonb,
  UNIQUE(tenant_id, plate_number)
);

ALTER TABLE vehicle ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vehicle
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);


-- 7b. Fleet Cost Profile (€/km, €/h etc.) ----------------------------------

CREATE TABLE fleet_cost_profile (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  vehicle_id    UUID REFERENCES vehicle(id),   -- NULL = applies to all vehicles
  valid_from    DATE NOT NULL,
  valid_until   DATE,
  euro_per_km          NUMERIC(8,4) NOT NULL,
  euro_per_hour_drive  NUMERIC(8,2),
  euro_per_hour_idle   NUMERIC(8,2),
  fixed_monthly_eur    NUMERIC(10,2),
  notes         TEXT,
  UNIQUE(tenant_id, vehicle_id, valid_from)
);

ALTER TABLE fleet_cost_profile ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fleet_cost_profile
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);


-- 7c. Route Trip (logical tour: departure → return to base) -----------------
-- Aggregated from route_stop records.

CREATE TABLE route_trip (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  upload_id     UUID REFERENCES upload(id),
  vehicle_id    UUID REFERENCES vehicle(id),

  trip_date     DATE NOT NULL,
  departure_time TIMESTAMPTZ,
  return_time    TIMESTAMPTZ,

  -- Aggregated metrics
  total_km          NUMERIC(10,2),
  total_drive_min   INTEGER,
  total_idle_min    INTEGER,
  stop_count        INTEGER,
  base_address      TEXT,             -- 'Haberstraße 14, 42551 Velbert'

  -- Calculated costs (from fleet_cost_profile)
  cost_km           NUMERIC(12,2),
  cost_time         NUMERIC(12,2),
  cost_total        NUMERIC(12,2),
  cost_per_stop     NUMERIC(12,2),    -- cost_total / stop_count

  meta              JSONB DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE route_trip ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON route_trip
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX idx_route_trip_tenant_date ON route_trip(tenant_id, trip_date);
CREATE INDEX idx_route_trip_vehicle ON route_trip(vehicle_id);


-- 7d. Route Stop (individual leg within a trip) -----------------------------

CREATE TABLE route_stop (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trip_id       UUID NOT NULL REFERENCES route_trip(id) ON DELETE CASCADE,
  stop_sequence INTEGER NOT NULL,      -- 1, 2, 3...

  -- Addresses
  departure_address   TEXT,
  departure_locality  VARCHAR(100),    -- named place: 'Zentrale', 'Servicecenter'
  arrival_address     TEXT,
  arrival_locality    VARCHAR(100),

  -- Extracted PLZ (for matching against carrier zones)
  departure_zip       VARCHAR(10),
  arrival_zip         VARCHAR(10),

  -- Timing
  departure_at        TIMESTAMPTZ,
  arrival_at          TIMESTAMPTZ,
  drive_min           INTEGER,
  idle_before_min     INTEGER,         -- Standdauer (before departure)
  idle_after_min      INTEGER,         -- Standdauer (after arrival)

  -- Distance
  distance_km         NUMERIC(10,3),

  -- Is this a customer delivery (vs. return to base, refuel, etc.)?
  is_delivery         BOOLEAN DEFAULT TRUE,

  meta                JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_route_stop_trip ON route_stop(trip_id, stop_sequence);


-- ============================================================================
-- 8. PARSING / TEMPLATE SYSTEM
-- ============================================================================

CREATE TABLE parsing_template (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID REFERENCES tenant(id),  -- NULL = global
  name            VARCHAR(255) NOT NULL,
  description     TEXT,
  file_type       VARCHAR(50) NOT NULL,
  template_category VARCHAR(50),               -- 'tariff', 'invoice', 'shipment_list', 'route'
  detection       JSONB NOT NULL,              -- rules to identify this format
  mappings        JSONB NOT NULL,              -- field extraction rules
  source          VARCHAR(50) DEFAULT 'manual',
  usage_count     INTEGER DEFAULT 0,
  last_used_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT now(),
  deleted_at      TIMESTAMPTZ
);

ALTER TABLE parsing_template ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON parsing_template
  USING (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant', true)::UUID);


-- ============================================================================
-- 9. CONSULTANT / REPORTING SUPPORT
-- ============================================================================

-- consultant_note and report are project children — RLS policies check via project FK.
CREATE TABLE consultant_note (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  note_type       VARCHAR(50) NOT NULL,        -- 'data_quality', 'anomaly', 'recommendation'
  content         TEXT NOT NULL,
  related_upload_id   UUID REFERENCES upload(id),
  related_shipment_id UUID REFERENCES shipment(id),
  priority        VARCHAR(20),
  status          VARCHAR(50) DEFAULT 'open',
  created_by      UUID NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now(),
  resolved_at     TIMESTAMPTZ
);

ALTER TABLE consultant_note ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON consultant_note
  USING (project_id IN (
    SELECT id FROM project
    WHERE tenant_id = current_setting('app.current_tenant', true)::UUID
  ));

CREATE TABLE report (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  version         INTEGER NOT NULL,
  report_type     VARCHAR(50) NOT NULL,
  title           VARCHAR(255),
  data_snapshot   JSONB NOT NULL,
  shipment_count  INTEGER,
  date_range_start DATE,
  date_range_end   DATE,
  generated_by    UUID NOT NULL,
  generated_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE(project_id, version)
);

ALTER TABLE report ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON report
  USING (project_id IN (
    SELECT id FROM project
    WHERE tenant_id = current_setting('app.current_tenant', true)::UUID
  ));


-- ============================================================================
-- 10. UTILITY: updated_at trigger
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_project_updated_at
  BEFORE UPDATE ON project FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_shipment_updated_at
  BEFORE UPDATE ON shipment FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ============================================================================
-- 11. SEED DATA
-- ============================================================================

-- Carriers from the sample documents
INSERT INTO carrier (name, code_norm, country) VALUES
  ('Cosi Stahllogistik GmbH & Co. KG', 'COSI', 'DE'),
  ('AS Stahl und Logistik GmbH & Co. KG', 'AS_STAHL', 'DE'),
  ('Gebrüder Weiss GmbH', 'GEBR_WEISS', 'AT')
ON CONFLICT (code_norm) DO NOTHING;

-- Common FX seed rates
INSERT INTO fx_rate (rate_date, from_ccy, to_ccy, rate, source) VALUES
  ('2023-01-01', 'EUR', 'CHF', 0.9850, 'manual'),
  ('2023-01-01', 'EUR', 'USD', 1.0650, 'manual'),
  ('2023-01-01', 'EUR', 'GBP', 0.8850, 'manual'),
  ('2023-01-01', 'EUR', 'PLN', 4.6800, 'manual')
ON CONFLICT DO NOTHING;


-- ============================================================================
-- Done.
-- ============================================================================
