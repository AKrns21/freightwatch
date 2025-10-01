-- migration: 003_refactor_to_project_workflow.sql
-- Purpose: Refactor FreightWatch to project-based workflow with LLM integration
-- Created: 2025-10-01

-- ============================================
-- STEP 1: Neue Tabellen hinzufügen
-- ============================================

-- Project table: Main workspace for consultants
CREATE TABLE project (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  name VARCHAR(255) NOT NULL,
  customer_name VARCHAR(255),
  phase VARCHAR(50) DEFAULT 'quick_check',
  status VARCHAR(50) DEFAULT 'draft',
  consultant_id UUID,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

ALTER TABLE project ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON project
  USING (tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX idx_project_tenant ON project(tenant_id);
CREATE INDEX idx_project_consultant ON project(consultant_id);
CREATE INDEX idx_project_phase ON project(phase);
CREATE INDEX idx_project_status ON project(status);
CREATE INDEX idx_project_deleted ON project(deleted_at) WHERE deleted_at IS NULL;

-- Consultant notes: Annotations for quality issues
CREATE TABLE consultant_note (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  note_type VARCHAR(50) NOT NULL,
  content TEXT NOT NULL,
  related_to_upload_id UUID REFERENCES upload(id),
  related_to_shipment_id UUID REFERENCES shipment(id),
  priority VARCHAR(20),
  status VARCHAR(50) DEFAULT 'open',
  created_by UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  resolved_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ
);

ALTER TABLE consultant_note ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON consultant_note
  USING (project_id IN (SELECT id FROM project WHERE tenant_id = current_setting('app.current_tenant')::UUID));

CREATE INDEX idx_note_project ON consultant_note(project_id);
CREATE INDEX idx_note_upload ON consultant_note(related_to_upload_id);
CREATE INDEX idx_note_shipment ON consultant_note(related_to_shipment_id);
CREATE INDEX idx_note_status ON consultant_note(status);
CREATE INDEX idx_note_deleted ON consultant_note(deleted_at) WHERE deleted_at IS NULL;

-- Parsing templates: Reusable file format definitions
CREATE TABLE parsing_template (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenant(id),
  name VARCHAR(255) NOT NULL,
  description TEXT,
  file_type VARCHAR(50) NOT NULL,
  template_category VARCHAR(50),
  detection JSONB NOT NULL,
  mappings JSONB NOT NULL,
  source VARCHAR(50) DEFAULT 'manual',
  verified_by UUID,
  verified_at TIMESTAMPTZ,
  usage_count INTEGER DEFAULT 0,
  last_used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

ALTER TABLE parsing_template ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON parsing_template
  USING (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant')::UUID);

CREATE INDEX idx_template_tenant ON parsing_template(tenant_id);
CREATE INDEX idx_template_category ON parsing_template(template_category);
CREATE INDEX idx_template_file_type ON parsing_template(file_type);
CREATE INDEX idx_template_deleted ON parsing_template(deleted_at) WHERE deleted_at IS NULL;

-- Manual mappings: Override automatic mapping decisions
CREATE TABLE manual_mapping (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  upload_id UUID NOT NULL REFERENCES upload(id) ON DELETE CASCADE,
  field_name VARCHAR(100) NOT NULL,
  source_column VARCHAR(100),
  mapping_rule JSONB,
  confidence DECIMAL(3,2),
  notes TEXT,
  created_by UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

ALTER TABLE manual_mapping ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON manual_mapping
  USING (upload_id IN (SELECT id FROM upload WHERE tenant_id = current_setting('app.current_tenant')::UUID));

CREATE INDEX idx_manual_mapping_upload ON manual_mapping(upload_id);
CREATE INDEX idx_manual_mapping_field ON manual_mapping(field_name);
CREATE INDEX idx_manual_mapping_deleted ON manual_mapping(deleted_at) WHERE deleted_at IS NULL;

-- Reports: Versioned report snapshots
CREATE TABLE report (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  report_type VARCHAR(50) NOT NULL,
  title VARCHAR(255),
  data_snapshot JSONB NOT NULL,
  data_completeness DECIMAL(3,2),
  shipment_count INTEGER,
  date_range_start DATE,
  date_range_end DATE,
  generated_by UUID NOT NULL,
  generated_at TIMESTAMPTZ DEFAULT now(),
  notes TEXT,
  deleted_at TIMESTAMPTZ,
  UNIQUE(project_id, version)
);

ALTER TABLE report ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON report
  USING (project_id IN (SELECT id FROM project WHERE tenant_id = current_setting('app.current_tenant')::UUID));

CREATE INDEX idx_report_project ON report(project_id);
CREATE INDEX idx_report_version ON report(project_id, version);
CREATE INDEX idx_report_type ON report(report_type);
CREATE INDEX idx_report_generated ON report(generated_at);
CREATE INDEX idx_report_deleted ON report(deleted_at) WHERE deleted_at IS NULL;

-- ============================================
-- STEP 2: Bestehende Tabellen erweitern
-- ============================================

-- Upload wird project-based und LLM-aware
ALTER TABLE upload ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES project(id);
ALTER TABLE upload ADD COLUMN IF NOT EXISTS parse_method VARCHAR(50);
ALTER TABLE upload ADD COLUMN IF NOT EXISTS confidence DECIMAL(3,2);
ALTER TABLE upload ADD COLUMN IF NOT EXISTS suggested_mappings JSONB;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS llm_analysis JSONB;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS reviewed_by UUID;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE upload ADD COLUMN IF NOT EXISTS parsing_issues JSONB;

CREATE INDEX IF NOT EXISTS idx_upload_project ON upload(project_id);
CREATE INDEX IF NOT EXISTS idx_upload_parse_method ON upload(parse_method);

-- Shipment wird quality-aware
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES project(id);
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS completeness_score DECIMAL(3,2);
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS missing_fields TEXT[];
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS data_quality_issues JSONB;
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS consultant_notes TEXT;
ALTER TABLE shipment ADD COLUMN IF NOT EXISTS manual_override BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_shipment_project ON shipment(project_id);
CREATE INDEX IF NOT EXISTS idx_shipment_completeness ON shipment(completeness_score);

-- Carrier bekommt Conversion-Rules (ersetzt tariff_rule)
ALTER TABLE carrier ADD COLUMN IF NOT EXISTS conversion_rules JSONB DEFAULT '{}'::jsonb;

-- ============================================
-- STEP 3: Service-Mapping vereinfachen
-- ============================================

-- service_level wird simple enum (keine normalization mehr nötig)
-- Die aktuellen Werte bleiben erhalten, nur der Datentyp wird angepasst
ALTER TABLE shipment ALTER COLUMN service_level TYPE VARCHAR(20);

-- NULL values werden zu STANDARD (fallback)
UPDATE shipment SET service_level = 'STANDARD' WHERE service_level IS NULL OR service_level = '';

-- ============================================
-- STEP 4: Alte Tabellen droppen
-- ============================================

-- Diese Tabellen werden durch neue Lösungen ersetzt:
-- - service_catalog/service_alias → einfache Enums
-- - surcharge_catalog → Parser-Logik
-- - tariff_rule → carrier.conversion_rules (JSONB)

DROP TABLE IF EXISTS service_alias CASCADE;
DROP TABLE IF EXISTS service_catalog CASCADE;
DROP TABLE IF EXISTS surcharge_catalog CASCADE;
DROP TABLE IF EXISTS tariff_rule CASCADE;

-- ============================================
-- STEP 5: Seed default data
-- ============================================

-- Insert global parsing templates for common formats
-- Note: Diese werden später durch LLM-generierte Templates ergänzt

INSERT INTO parsing_template (tenant_id, name, description, file_type, template_category, detection, mappings, source)
VALUES
  (
    NULL,
    'Generic CSV Shipment List',
    'Generic template for CSV files with shipment data',
    'csv',
    'shipment_list',
    '{"has_headers": true, "min_columns": 5}'::jsonb,
    '{
      "date": {"keywords": ["datum", "date", "shipment_date"], "format": "dd.mm.yyyy"},
      "carrier_name": {"keywords": ["carrier", "spedition", "carrier_name"]},
      "origin_zip": {"keywords": ["from", "origin", "sender_zip", "plz_von"]},
      "dest_zip": {"keywords": ["to", "destination", "receiver_zip", "plz_nach"]},
      "weight_kg": {"keywords": ["weight", "gewicht", "kg"]},
      "actual_cost": {"keywords": ["cost", "price", "kosten", "betrag"]}
    }'::jsonb,
    'system'
  );

-- ============================================
-- STEP 6: Migration von carrier-spezifischen Regeln
-- ============================================

-- Falls tariff_rule Daten existieren, migriere sie zu carrier.conversion_rules
-- Dies wird nur ausgeführt wenn die Tabelle noch existiert

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tariff_rule') THEN
    -- Aggregiere alle Regeln pro Carrier in ein JSONB Objekt
    UPDATE carrier c
    SET conversion_rules = (
      SELECT jsonb_object_agg(rule_type, param_json)
      FROM tariff_rule tr
      WHERE tr.carrier_id = c.id
      GROUP BY tr.carrier_id
    )
    WHERE EXISTS (
      SELECT 1 FROM tariff_rule tr WHERE tr.carrier_id = c.id
    );
  END IF;
END $$;

-- ============================================
-- STEP 7: Data integrity checks
-- ============================================

-- Verify RLS policies are enabled
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_tables
    WHERE schemaname = 'public'
    AND tablename = 'project'
    AND rowsecurity = true
  ) THEN
    RAISE EXCEPTION 'RLS not enabled on project table';
  END IF;
END $$;

-- ============================================
-- STEP 8: Update trigger for updated_at
-- ============================================

-- Add trigger for project.updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_project_updated_at
  BEFORE UPDATE ON project
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- Migration Complete
-- ============================================

-- Summary of changes:
-- ✅ 5 new tables: project, consultant_note, parsing_template, manual_mapping, report
-- ✅ Extended upload with LLM fields
-- ✅ Extended shipment with quality tracking
-- ✅ Extended carrier with conversion_rules
-- ✅ Dropped 4 legacy tables: service_catalog, service_alias, surcharge_catalog, tariff_rule
-- ✅ RLS policies enabled on all new tables
-- ✅ Indexes created for performance
-- ✅ Default templates seeded
