-- ============================================================================
-- 020: Create users table + manual_mapping + tariff_table.confidence
-- Date: 2026-03-19
--
-- Fixes three schema gaps between ORM models and actual database:
-- 1. users table (never in 001_fresh_schema, was created manually for NestJS)
-- 2. manual_mapping table (referenced by ORM, never migrated)
-- 3. tariff_table.confidence column (ORM expects it, DB lacks it)
-- ============================================================================

-- 1. Users table (tenant-scoped, RLS enabled) --------------------------------

CREATE TABLE IF NOT EXISTS users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  first_name    VARCHAR(100),
  last_name     VARCHAR(100),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  roles         JSONB,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  last_login_at TIMESTAMPTZ,
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- Login must work WITHOUT tenant context (user doesn't know their tenant yet),
-- so SELECT uses a permissive policy. Write operations are tenant-scoped.
CREATE POLICY users_select ON users FOR SELECT USING (TRUE);
CREATE POLICY users_tenant_write ON users FOR ALL
  USING (tenant_id = current_setting('app.current_tenant', true)::UUID);

CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TRIGGER trg_users_updated_at
  BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- 2. Manual mapping table (tenant-scoped via upload) --------------------------

CREATE TABLE IF NOT EXISTS manual_mapping (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  upload_id     UUID NOT NULL REFERENCES upload(id),
  field_name    VARCHAR(100) NOT NULL,
  source_column VARCHAR(100),
  mapping_rule  JSONB,
  confidence    NUMERIC(3,2),
  notes         TEXT,
  created_by    UUID NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT now(),
  deleted_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_manual_mapping_upload ON manual_mapping(upload_id);
CREATE INDEX IF NOT EXISTS idx_manual_mapping_field ON manual_mapping(field_name);


-- 3. Add missing confidence column to tariff_table ---------------------------

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'tariff_table'
      AND column_name = 'confidence'
  ) THEN
    ALTER TABLE tariff_table ADD COLUMN confidence NUMERIC(3,2);
  END IF;
END $$;
