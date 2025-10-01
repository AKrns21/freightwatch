-- Minimal initialization for testing refactoring migration
-- This is a simplified version to test migration 003

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

CREATE TABLE tenant (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  settings JSONB DEFAULT '{"currency": "EUR", "default_diesel_floater": 0.185, "country": "DE"}'::jsonb
);

ALTER TABLE tenant ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant
  USING (id = current_setting('app.current_tenant')::UUID);

CREATE TABLE fx_rate (
  rate_date DATE NOT NULL,
  from_ccy CHAR(3) NOT NULL,
  to_ccy CHAR(3) NOT NULL,
  rate NUMERIC(18,8) NOT NULL,
  source TEXT,
  PRIMARY KEY(rate_date, from_ccy, to_ccy)
);

CREATE TABLE carrier (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  code_norm VARCHAR(50) UNIQUE NOT NULL,
  country VARCHAR(2)
);

CREATE TABLE upload (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  filename VARCHAR(500) NOT NULL,
  file_hash VARCHAR(64) NOT NULL,
  mime_type VARCHAR(100),
  source_type VARCHAR(50),
  received_at TIMESTAMPTZ DEFAULT now(),
  storage_url TEXT,
  status VARCHAR(50) DEFAULT 'pending',
  parse_errors JSONB,
  raw_text_hash VARCHAR(64),
  UNIQUE(tenant_id, file_hash)
);

ALTER TABLE upload ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON upload
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE shipment (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  upload_id UUID REFERENCES upload(id),
  date DATE NOT NULL,
  carrier_id UUID REFERENCES carrier(id),
  service_level VARCHAR(50),
  origin_zip VARCHAR(10),
  origin_country VARCHAR(2) DEFAULT 'DE',
  dest_zip VARCHAR(10),
  dest_country VARCHAR(2) DEFAULT 'DE',
  weight_kg DECIMAL(10,2),
  ldm DECIMAL(8,2),
  currency CHAR(3) NOT NULL DEFAULT 'EUR',
  actual_total_amount DECIMAL(12,2) NOT NULL,
  source_data JSONB,
  meta JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

ALTER TABLE shipment ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shipment
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX idx_shipment_tenant ON shipment(tenant_id);
CREATE INDEX idx_shipment_upload ON shipment(upload_id);
CREATE INDEX idx_shipment_carrier ON shipment(carrier_id);
CREATE INDEX idx_shipment_date ON shipment(date);
CREATE INDEX idx_shipment_deleted ON shipment(deleted_at) WHERE deleted_at IS NULL;

CREATE TABLE shipment_benchmark (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shipment_id UUID NOT NULL REFERENCES shipment(id),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  tariff_table_id UUID,
  expected_base_amount DECIMAL(10,2),
  expected_diesel_amount DECIMAL(10,2),
  expected_toll_amount DECIMAL(10,2),
  expected_total_amount DECIMAL(10,2),
  actual_total_amount DECIMAL(12,2),
  delta_amount DECIMAL(12,2),
  delta_pct DECIMAL(6,2),
  classification VARCHAR(20),
  diesel_basis_used VARCHAR(50),
  diesel_pct_used DECIMAL(5,2),
  fx_rate_used NUMERIC(18,8),
  fx_rate_date DATE,
  cost_breakdown JSONB,
  calc_version VARCHAR(10),
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE shipment_benchmark ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shipment_benchmark
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE tariff_zone_map (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID NOT NULL REFERENCES carrier(id),
  country VARCHAR(2) NOT NULL,
  plz_prefix VARCHAR(5) NOT NULL,
  prefix_len INT,
  zone INT NOT NULL,
  valid_from DATE DEFAULT CURRENT_DATE,
  valid_until DATE
);

ALTER TABLE tariff_zone_map ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_zone_map
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE tariff_table (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID NOT NULL REFERENCES carrier(id),
  lane_type VARCHAR(50) NOT NULL,
  zone INT NOT NULL,
  weight_min DECIMAL(10,2) NOT NULL,
  weight_max DECIMAL(10,2) NOT NULL,
  base_amount DECIMAL(10,2) NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'EUR',
  valid_from DATE DEFAULT CURRENT_DATE,
  valid_until DATE
);

ALTER TABLE tariff_table ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_table
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE diesel_floater (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID REFERENCES carrier(id),
  valid_from DATE NOT NULL,
  valid_until DATE,
  pct DECIMAL(5,2) NOT NULL,
  basis VARCHAR(50) DEFAULT 'base'
);

ALTER TABLE diesel_floater ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON diesel_floater
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE invoice_header (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  upload_id UUID REFERENCES upload(id),
  invoice_number VARCHAR(100),
  invoice_date DATE,
  carrier_id UUID REFERENCES carrier(id),
  total_amount DECIMAL(12,2),
  currency CHAR(3),
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE invoice_header ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON invoice_header
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE TABLE invoice_line (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  invoice_header_id UUID NOT NULL REFERENCES invoice_header(id),
  shipment_id UUID REFERENCES shipment(id),
  line_number INT,
  description TEXT,
  amount DECIMAL(12,2),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE service_catalog (
  code VARCHAR(50) PRIMARY KEY,
  description VARCHAR(255),
  category VARCHAR(50)
);

CREATE TABLE service_alias (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenant(id),
  carrier_id UUID REFERENCES carrier(id),
  alias_text VARCHAR(100) NOT NULL,
  service_code VARCHAR(50) NOT NULL REFERENCES service_catalog(code)
);

CREATE TABLE surcharge_catalog (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenant(id),
  carrier_id UUID REFERENCES carrier(id),
  surcharge_type VARCHAR(100),
  description VARCHAR(255)
);

CREATE TABLE tariff_rule (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  carrier_id UUID REFERENCES carrier(id),
  rule_type VARCHAR(50) NOT NULL,
  param_json JSONB NOT NULL
);

ALTER TABLE tariff_rule ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tariff_rule
  USING (tenant_id = current_setting('app.current_tenant')::UUID);
