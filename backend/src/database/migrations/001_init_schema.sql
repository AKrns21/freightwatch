-- FreightWatch Initial Schema Migration
-- Version: 1.0
-- Date: 2024-09-30

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

-- ==============================================
-- TENANT SYSTEM
-- ==============================================

CREATE TABLE tenant (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  settings JSONB DEFAULT jsonb_build_object(
    'currency', 'EUR',
    'default_diesel_floater', 0.185,
    'country', 'DE',
    'data_retention_days', 2555
  )
);

-- Enable RLS on tenant table
ALTER TABLE tenant ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant
  USING (id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- FOREIGN EXCHANGE RATES
-- ==============================================

CREATE TABLE fx_rate (
  rate_date DATE NOT NULL,
  from_ccy CHAR(3) NOT NULL,
  to_ccy CHAR(3) NOT NULL,
  rate NUMERIC(18,8) NOT NULL,
  source TEXT, -- 'ecb', 'manual', 'api'
  
  PRIMARY KEY(rate_date, from_ccy, to_ccy)
);

-- ==============================================
-- FILE UPLOADS
-- ==============================================

CREATE TABLE upload (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  filename VARCHAR(500) NOT NULL,
  file_hash VARCHAR(64) NOT NULL, -- SHA256
  mime_type VARCHAR(100),
  source_type VARCHAR(50), -- 'invoice', 'rate_card', 'fleet_log'
  received_at TIMESTAMPTZ DEFAULT now(),
  storage_url TEXT,
  status VARCHAR(50) DEFAULT 'pending', -- 'pending', 'parsed', 'unmatched', 'failed'
  parse_errors JSONB,
  raw_text_hash VARCHAR(64), -- für unmatched carrier templates
  
  UNIQUE(tenant_id, file_hash) -- Idempotenz
);

-- Enable RLS on upload table
ALTER TABLE upload ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON upload
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- CARRIER SYSTEM
-- ==============================================

CREATE TABLE carrier (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  code_norm VARCHAR(50) UNIQUE NOT NULL, -- 'COSI', 'AS_STAHL', 'GEBR_WEISS'
  country VARCHAR(2)
);

CREATE TABLE carrier_alias (
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  alias_text VARCHAR(255) NOT NULL,
  carrier_id UUID NOT NULL REFERENCES carrier(id),
  PRIMARY KEY (tenant_id, alias_text)
);

-- ==============================================
-- ZONE MAPPING SYSTEM
-- ==============================================

CREATE TABLE tariff_zone_map (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID NOT NULL REFERENCES carrier(id),
  
  country VARCHAR(2) NOT NULL, -- 'DE', 'AT', 'CH', 'FR', 'IT', 'GB'
  
  -- Flexible PLZ-Mapping
  plz_prefix VARCHAR(5) NOT NULL, -- '42', '78', '83' oder '875' (3-stellig)
  prefix_len INT, -- 2, 3, 4 - Länge des Prefix
  pattern TEXT, -- Optional: Regex für komplexe Fälle (UK postcodes etc.)
  
  zone INTEGER NOT NULL,
  
  valid_from DATE NOT NULL,
  valid_until DATE,
  
  UNIQUE(tenant_id, carrier_id, country, plz_prefix, valid_from)
);

-- Enable RLS on tariff_zone_map table
ALTER TABLE tariff_zone_map ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_zone_map
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- SERVICE CATALOG SYSTEM
-- ==============================================

CREATE TABLE service_catalog (
  code VARCHAR(50) PRIMARY KEY, -- 'STANDARD', 'EXPRESS', 'ECONOMY', 'NEXT_DAY', 'SAME_DAY'
  description TEXT,
  category VARCHAR(50) -- 'standard', 'premium', 'economy'
);

CREATE TABLE service_alias (
  tenant_id UUID REFERENCES tenant(id), -- NULL = global
  carrier_id UUID REFERENCES carrier(id), -- NULL = alle Carrier
  
  alias_text VARCHAR(100) NOT NULL, -- '24h', 'Next Day', 'Express', 'Overnight'
  service_code VARCHAR(50) NOT NULL REFERENCES service_catalog(code),
  
  PRIMARY KEY (COALESCE(tenant_id::text, 'global'), COALESCE(carrier_id::text, 'all'), alias_text)
);

-- ==============================================
-- DIESEL FLOATER SYSTEM
-- ==============================================

CREATE TABLE diesel_floater (
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID NOT NULL REFERENCES carrier(id),
  
  valid_from DATE NOT NULL,
  valid_until DATE, -- NULL = unbegrenzt gültig bis nächster Eintrag
  
  pct DECIMAL(5,2) NOT NULL,
  
  -- Basis für Diesel-Berechnung
  basis TEXT DEFAULT 'base' CHECK (basis IN ('base', 'base_plus_toll', 'total')),
  
  applies_to TEXT DEFAULT 'shipment' CHECK (applies_to IN ('shipment', 'leg', 'zone')),
  
  source VARCHAR(100), -- 'invoice', 'email', 'manual', 'website'
  notes TEXT,
  
  PRIMARY KEY (tenant_id, carrier_id, valid_from)
);

-- Enable RLS on diesel_floater table
ALTER TABLE diesel_floater ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON diesel_floater
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- INVOICE SYSTEM
-- ==============================================

CREATE TABLE invoice_header (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID REFERENCES carrier(id),
  
  invoice_no TEXT NOT NULL,
  invoice_date DATE NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'EUR',
  
  total_net NUMERIC(12,2),
  total_tax NUMERIC(12,2),
  total_gross NUMERIC(12,2),
  
  source_upload_id UUID REFERENCES upload(id),
  status VARCHAR(50) DEFAULT 'pending',
  
  created_at TIMESTAMPTZ DEFAULT now(),
  
  UNIQUE(tenant_id, carrier_id, invoice_no, invoice_date)
);

-- Enable RLS on invoice_header table
ALTER TABLE invoice_header ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invoice_header
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE invoice_line (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  header_id UUID NOT NULL REFERENCES invoice_header(id) ON DELETE CASCADE,
  
  line_no INT,
  reference_number TEXT, -- Auftragsnummer, Sendungsnummer
  description TEXT,
  
  amount NUMERIC(12,2) NOT NULL,
  surcharge_type TEXT, -- 'base', 'diesel', 'toll', 'length', 'other'
  
  meta JSONB -- weitere Felder aus Rechnung
);

-- ==============================================
-- SHIPMENT SYSTEM
-- ==============================================

CREATE TABLE shipment (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  upload_id UUID NOT NULL REFERENCES upload(id),
  invoice_line_id UUID REFERENCES invoice_line(id), -- Link zur Rechnungszeile
  
  -- Metadata
  date DATE NOT NULL,
  carrier_id UUID REFERENCES carrier(id),
  service_level VARCHAR(50), -- normiert: 'standard', 'express', 'economy'
  reference_number VARCHAR(100),
  
  -- Route
  origin_zip VARCHAR(10),
  origin_country VARCHAR(2) DEFAULT 'DE',
  dest_zip VARCHAR(10),
  dest_country VARCHAR(2) DEFAULT 'DE',
  zone_de INTEGER, -- 1-6 für DE
  zone_at INTEGER, -- für AT
  
  -- Dimensions
  weight_kg DECIMAL(10,2),
  volume_cbm DECIMAL(10,3),
  pallets DECIMAL(5,2),
  length_m DECIMAL(5,2), -- Längenzuschlag
  
  -- Chargeable Basis
  chargeable_basis VARCHAR(20), -- 'kg', 'lm', 'pallet', 'min_weight', 'volumetric'
  chargeable_weight_kg DECIMAL(10,2), -- berechnetes Gewicht
  
  -- Actual Costs (from invoice) in ORIGINAL currency
  currency CHAR(3) DEFAULT 'EUR',
  actual_total_amount DECIMAL(10,2), -- renamed from actual_total_eur
  actual_base_amount DECIMAL(10,2),
  diesel_pct DECIMAL(5,2),
  diesel_amount DECIMAL(10,2),
  toll_amount DECIMAL(10,2), -- renamed from toll_eur: generisch Maut/Straßengebühr
  toll_country VARCHAR(2), -- DE, FR, IT, CH, ...
  other_surcharge_amount DECIMAL(10,2),
  
  -- FX Conversion (für Reporting in Tenant-Currency)
  fx_rate NUMERIC(18,8), -- Original → Tenant Currency
  report_currency CHAR(3), -- Tenant reporting currency
  report_amounts_cached JSONB, -- {total, base, diesel, toll, other} in report_currency
  
  -- Data Lineage
  source_data JSONB, -- raw extracted data
  extraction_method VARCHAR(50), -- 'csv_direct', 'pdf_regex', 'ocr'
  confidence_score DECIMAL(3,2),
  
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Enable RLS on shipment table
ALTER TABLE shipment ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shipment
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- TARIFF SYSTEM
-- ==============================================

CREATE TABLE tariff_table (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID NOT NULL REFERENCES carrier(id),
  
  lane_type VARCHAR(20) NOT NULL, -- 'domestic_de', 'domestic_at', 'de_to_ch', 'de_to_at'
  currency CHAR(3) NOT NULL DEFAULT 'EUR',
  valid_from DATE NOT NULL,
  valid_until DATE,
  
  basis VARCHAR(20) NOT NULL, -- 'kg', 'lm', 'pallet'
  
  -- Zone & Weight Matrix
  zone INTEGER NOT NULL,
  weight_from_kg DECIMAL(10,2) NOT NULL,
  weight_to_kg DECIMAL(10,2) NOT NULL,
  
  price NUMERIC(10,2) NOT NULL, -- in currency
  
  -- Metadata
  source_upload_id UUID REFERENCES upload(id),
  notes TEXT,
  
  -- Prevent overlapping tariffs
  EXCLUDE USING gist (
    carrier_id WITH =,
    lane_type WITH =,
    zone WITH =,
    int4range(CEIL(weight_from_kg)::int, CEIL(weight_to_kg)::int, '[]') WITH &&,
    tstzrange(valid_from::timestamptz, COALESCE(valid_until::timestamptz, 'infinity'::timestamptz)) WITH &&
  )
);

-- Enable RLS on tariff_table
ALTER TABLE tariff_table ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_table
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE tariff_rule (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID NOT NULL REFERENCES carrier(id),
  
  rule_type VARCHAR(50) NOT NULL, 
  -- 'ldm_conversion', 'min_pallet_weight', 'length_surcharge', 'island_adr_exclusion'
  
  param_json JSONB NOT NULL,
  -- e.g. {"ldm_to_kg": 1850} (1 ldm = 1850 kg für diesen Carrier)
  -- e.g. {"min_weight_per_pallet_kg": 300}
  -- e.g. {"length_over_m": 3, "surcharge_eur": 30}
  
  valid_from DATE NOT NULL,
  valid_until DATE
);

-- Enable RLS on tariff_rule
ALTER TABLE tariff_rule ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_rule
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- SURCHARGE CATALOG
-- ==============================================

CREATE TABLE surcharge_catalog (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenant(id), -- NULL = global
  carrier_id UUID REFERENCES carrier(id), -- NULL = alle Carrier
  
  surcharge_type VARCHAR(50) NOT NULL, -- 'diesel', 'toll', 'length', 'adr', 'island'
  
  -- Extraction Pattern
  regex_pattern TEXT,
  label_aliases TEXT[], -- ['Dieselzuschlag', 'Diesel-Aufschlag', 'Treibstoffzuschlag']
  
  notes TEXT
);

-- ==============================================
-- BENCHMARKING SYSTEM
-- ==============================================

CREATE TABLE shipment_benchmark (
  shipment_id UUID PRIMARY KEY REFERENCES shipment(id),
  tenant_id UUID NOT NULL, -- denormalized für RLS
  tariff_table_id UUID REFERENCES tariff_table(id),
  calc_version VARCHAR(20) DEFAULT '1.0',
  
  -- Expected Costs (Sollkosten) - in shipment currency
  expected_base_amount DECIMAL(10,2),
  expected_diesel_amount DECIMAL(10,2),
  expected_toll_amount DECIMAL(10,2), -- FIXED: war expected_maut_eur
  expected_total_amount DECIMAL(10,2),
  
  -- Calculation Metadata (Snapshot-Nachvollziehbarkeit)
  diesel_basis_used TEXT, -- 'base', 'base_plus_toll', 'total'
  diesel_pct_used DECIMAL(5,2),
  fx_rate_used NUMERIC(18,8),
  fx_rate_date DATE,
  
  -- Itemization (Nachvollziehbarkeit)
  cost_breakdown JSONB,
  -- e.g. [
  --   {"item": "base_rate", "zone": 3, "weight": 450, "price": 294.30},
  --   {"item": "diesel_surcharge", "base": 294.30, "pct": 18.5, "value": 54.45},
  --   {"item": "toll", "value": 15.20, "note": "from_invoice"}
  -- ]
  
  -- Delta (in shipment currency)
  delta_amount DECIMAL(10,2),
  delta_pct DECIMAL(5,2),
  classification VARCHAR(20), -- 'unter', 'im_markt', 'drüber'
  
  calculated_at TIMESTAMPTZ DEFAULT now()
);

-- Enable RLS on shipment_benchmark
ALTER TABLE shipment_benchmark ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shipment_benchmark
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- FLEET SYSTEM
-- ==============================================

CREATE TABLE fleet_cost_profile (
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  valid_from DATE NOT NULL,
  
  euro_per_km DECIMAL(8,2) NOT NULL,
  euro_per_hour_idle DECIMAL(8,2) NOT NULL,
  fixed_monthly_eur DECIMAL(10,2),
  
  notes TEXT,
  PRIMARY KEY (tenant_id, valid_from)
);

CREATE TABLE fleet_journey (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  upload_id UUID NOT NULL REFERENCES upload(id),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  
  date DATE NOT NULL,
  vehicle VARCHAR(100),
  
  km DECIMAL(8,2),
  drive_hours DECIMAL(6,2),
  idle_hours DECIMAL(6,2),
  stops INTEGER,
  
  -- Calculated
  cost_km_eur DECIMAL(10,2),
  cost_idle_eur DECIMAL(10,2),
  total_cost_eur DECIMAL(10,2)
);

-- Enable RLS on fleet_journey
ALTER TABLE fleet_journey ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fleet_journey
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

-- ==============================================
-- INDEXES
-- ==============================================

-- FX Rate indexes
CREATE INDEX idx_fx_recent ON fx_rate(from_ccy, to_ccy, rate_date DESC);

-- Zone mapping indexes
CREATE INDEX idx_zone_lookup ON tariff_zone_map(
  tenant_id, carrier_id, country, prefix_len, plz_prefix
);

CREATE INDEX idx_zone_pattern ON tariff_zone_map(
  tenant_id, carrier_id, country, pattern
) WHERE pattern IS NOT NULL;

-- Diesel floater indexes
CREATE INDEX idx_diesel_lookup ON diesel_floater(
  tenant_id, carrier_id, valid_from DESC
);

-- Invoice indexes
CREATE INDEX idx_invoice_tenant ON invoice_header(tenant_id);
CREATE INDEX idx_invoice_carrier ON invoice_header(carrier_id);
CREATE INDEX idx_invoice_date ON invoice_header(invoice_date);

CREATE INDEX idx_line_header ON invoice_line(header_id);
CREATE INDEX idx_line_ref ON invoice_line(reference_number);

-- Shipment indexes
CREATE INDEX idx_shipment_tenant ON shipment(tenant_id);
CREATE INDEX idx_shipment_date ON shipment(date);
CREATE INDEX idx_shipment_carrier ON shipment(carrier_id);
CREATE INDEX idx_shipment_zone ON shipment(zone_de);
CREATE INDEX idx_shipment_invoice ON shipment(invoice_line_id);

-- Tariff indexes
CREATE INDEX idx_tariff_lookup ON tariff_table(
  tenant_id, carrier_id, lane_type, zone, weight_from_kg, weight_to_kg
);

-- Benchmark indexes
CREATE INDEX idx_benchmark_tenant ON shipment_benchmark(tenant_id, classification);
CREATE INDEX idx_benchmark_date ON shipment_benchmark(tenant_id, calculated_at);

-- ==============================================
-- BACKWARD COMPATIBILITY VIEWS
-- ==============================================

-- Shipment legacy view (optional, für Migration)
CREATE VIEW shipment_legacy AS 
SELECT 
  *,
  actual_total_amount AS actual_total_eur,
  actual_base_amount AS actual_base_eur,
  diesel_amount AS diesel_eur,
  toll_amount AS toll_eur,
  other_surcharge_amount AS other_surcharge_eur
FROM shipment;

-- Benchmark legacy view (optional)
CREATE VIEW shipment_benchmark_legacy AS
SELECT
  *,
  expected_base_amount AS expected_base_eur,
  expected_diesel_amount AS expected_diesel_eur,
  expected_toll_amount AS expected_maut_eur,
  expected_total_amount AS expected_total_eur,
  delta_amount AS delta_eur
FROM shipment_benchmark;

-- ==============================================
-- SEED DATA
-- ==============================================

-- Seed carriers
INSERT INTO carrier (name, code_norm, country) VALUES
  ('Cosi Stahllogistik', 'COSI', 'DE'),
  ('AS Stahl und Logistik', 'AS_STAHL', 'DE'),
  ('Gebrüder Weiss', 'GEBR_WEISS', 'DE'),
  ('TNT Express', 'TNT', 'INTL'),
  ('tele Logistics', 'TELE', 'EU');

-- Seed service catalog
INSERT INTO service_catalog (code, description, category) VALUES
  ('STANDARD', 'Standard Delivery', 'standard'),
  ('EXPRESS', 'Express/Next Day', 'premium'),
  ('ECONOMY', 'Economy/Slow', 'economy'),
  ('NEXT_DAY', 'Next Day Delivery', 'premium'),
  ('SAME_DAY', 'Same Day Delivery', 'premium'),
  ('PREMIUM', 'Premium Service', 'premium'),
  ('ECO', 'Eco-Friendly Slow', 'economy');

-- Seed global service aliases
INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES
  (NULL, NULL, '24h', 'EXPRESS'),
  (NULL, NULL, 'next day', 'NEXT_DAY'),
  (NULL, NULL, 'overnight', 'NEXT_DAY'),
  (NULL, NULL, 'express', 'EXPRESS'),
  (NULL, NULL, 'premium', 'PREMIUM'),
  (NULL, NULL, 'standard', 'STANDARD'),
  (NULL, NULL, 'normal', 'STANDARD'),
  (NULL, NULL, 'eco', 'ECONOMY'),
  (NULL, NULL, 'economy', 'ECONOMY'),
  (NULL, NULL, 'slow', 'ECONOMY');