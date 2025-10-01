# Database Patterns

This guide covers PostgreSQL schema design, Row-Level Security (RLS), migrations, and query patterns for FreightWatch.

## Core Principles

1. **PostgreSQL 14+ only** - No MongoDB, no other databases
2. **RLS enforced on all tenant-scoped tables** - Zero trust model
3. **JSONB for unstructured data** - Raw parsed content, flexible metadata
4. **Temporal validity** - Use `valid_from`/`valid_until`, never month columns
5. **Soft deletes** - `deleted_at` timestamp, never hard DELETE

## Row-Level Security (RLS)

### Why RLS?

Manual tenant filtering is error-prone:
```typescript
// ❌ DANGEROUS: Easy to forget WHERE clause
const shipments = await repo.find({ carrier_id: carrierId });
// Returns ALL tenants' data if carrier_id matches!

// ✅ RLS enforces: Automatically filters by tenant_id
await db.setTenantContext(tenantId);
const shipments = await repo.find({ carrier_id: carrierId });
// Only returns data for tenantId, even if you forget to add WHERE clause
```

### RLS Implementation Pattern

**1. Enable RLS on table**
```sql
CREATE TABLE shipment (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  -- ... other columns
);

ALTER TABLE shipment ENABLE ROW LEVEL SECURITY;
```

**2. Create policy**
```sql
CREATE POLICY tenant_isolation ON shipment
  USING (tenant_id = current_setting('app.current_tenant')::UUID);
```

**3. Grant permissions**
```sql
-- Application user can only see/modify own tenant's data
GRANT SELECT, INSERT, UPDATE, DELETE ON shipment TO app_user;
```

**4. Set tenant context before queries**
```typescript
// In TenantInterceptor (runs on every HTTP request)
async setTenantContext(tenantId: string): Promise<void> {
  await this.db.query(
    'SET LOCAL app.current_tenant = $1',
    [tenantId]
  );
}

// Or in tests
beforeEach(async () => {
  await db.setTenantContext(tenant1.id);
});
```

### Testing RLS Isolation

**Critical test: Verify cross-tenant isolation**
```typescript
describe('RLS Isolation', () => {
  it('prevents cross-tenant data access', async () => {
    const tenant1 = await createTenant({ name: 'Tenant A' });
    const tenant2 = await createTenant({ name: 'Tenant B' });
    
    // Create shipment for tenant1
    await db.setTenantContext(tenant1.id);
    const shipment = await shipmentRepo.save({
      tenant_id: tenant1.id,
      carrier_id: carrier.id,
      // ... other fields
    });
    
    // Switch to tenant2
    await db.setTenantContext(tenant2.id);
    
    // Should NOT find tenant1's shipment
    const found = await shipmentRepo.findOne({ id: shipment.id });
    expect(found).toBeNull();
  });
  
  it('allows same-tenant data access', async () => {
    await db.setTenantContext(tenant1.id);
    const shipment = await shipmentRepo.save({ /* ... */ });
    
    // Same tenant can access
    const found = await shipmentRepo.findOne({ id: shipment.id });
    expect(found).toBeDefined();
  });
});
```

### Tables with RLS

All these tables MUST have RLS enabled:
- `tenant` (self-referential: can only see own record)
- `user` (via `tenant_id`)
- `carrier` (via `tenant_id`, NULL = global carriers)
- `upload`
- `invoice_header`
- `invoice_line`
- `shipment`
- `shipment_benchmark`
- `tariff_table`
- `tariff_zone_map`
- `tariff_rule`
- `diesel_floater`
- `fx_rate` (shared read-only, no tenant_id)

### Tables WITHOUT RLS

These are global reference data:
- `service_catalog` (read-only, no tenant_id)
- `service_alias` (global aliases when tenant_id IS NULL)

## Schema Design Patterns

### 1. Tenant-Scoped Tables

**Standard pattern:**
```sql
CREATE TABLE <table_name> (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(id),
  
  -- Business columns
  
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  deleted_at TIMESTAMPTZ -- Soft delete
);

CREATE INDEX idx_<table>_tenant ON <table_name>(tenant_id);
CREATE INDEX idx_<table>_deleted ON <table_name>(deleted_at) 
  WHERE deleted_at IS NULL; -- Partial index for active records

ALTER TABLE <table_name> ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON <table_name>
  USING (tenant_id = current_setting('app.current_tenant')::UUID);
```

### 2. Global Reference Data with Tenant Overrides

Example: `carrier` table can have global carriers (NULL tenant_id) plus tenant-specific custom carriers.

```sql
CREATE TABLE carrier (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenant(id), -- NULL = global
  
  name VARCHAR(100) NOT NULL,
  code VARCHAR(50),
  
  UNIQUE(tenant_id, code) -- Allow same code across tenants
);

-- RLS: See global carriers + own tenant's carriers
CREATE POLICY carrier_access ON carrier
  USING (
    tenant_id IS NULL -- Global carriers visible to all
    OR tenant_id = current_setting('app.current_tenant')::UUID
  );
```

### 3. Temporal Validity Pattern

**Don't use month/year columns:**
```sql
-- ❌ BAD: What if diesel changes mid-month?
CREATE TABLE diesel_floater (
  carrier_id UUID,
  month VARCHAR(7), -- '2024-01'
  pct DECIMAL(5,2)
);
```

**Use date ranges:**
```sql
-- ✅ GOOD: Supports any change frequency
CREATE TABLE diesel_floater (
  tenant_id UUID NOT NULL,
  carrier_id UUID NOT NULL,
  
  valid_from DATE NOT NULL,
  valid_until DATE, -- NULL = currently valid
  
  pct DECIMAL(5,2) NOT NULL,
  basis TEXT DEFAULT 'base',
  
  PRIMARY KEY (tenant_id, carrier_id, valid_from)
);

-- Query for date
SELECT pct, basis 
FROM diesel_floater
WHERE tenant_id = $1 
  AND carrier_id = $2
  AND valid_from <= $3
  AND (valid_until IS NULL OR valid_until >= $3)
ORDER BY valid_from DESC
LIMIT 1;
```

### 4. JSONB for Flexible Data

**Use cases:**
- Raw parsed data (varies by file format)
- Tariff rule parameters (varies by carrier)
- Calculation breakdown (audit trail)
- Feature flags (per-tenant settings)

```sql
CREATE TABLE shipment (
  -- ... standard columns
  
  source_data JSONB, -- Raw CSV/PDF row as parsed
  meta JSONB DEFAULT '{}'::jsonb
);

-- GIN index for fast JSONB queries
CREATE INDEX idx_shipment_source_data ON shipment USING GIN (source_data);

-- Query by JSONB field
SELECT * FROM shipment 
WHERE source_data->>'reference_number' = 'MECU-12345';

-- Query by nested path
SELECT * FROM shipment 
WHERE source_data->'metadata'->>'priority' = 'high';
```

**Example: Tariff rules as JSONB**
```sql
CREATE TABLE tariff_rule (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  carrier_id UUID,
  
  rule_type VARCHAR(50) NOT NULL, -- 'ldm_conversion', 'min_pallet_weight', ...
  param_json JSONB NOT NULL,
  
  UNIQUE(tenant_id, carrier_id, rule_type)
);

-- Insert LDM conversion rule
INSERT INTO tariff_rule (tenant_id, carrier_id, rule_type, param_json)
VALUES (
  'tenant-uuid',
  'carrier-uuid',
  'ldm_conversion',
  '{"ldm_to_kg": 1850}'::jsonb
);

-- Query
SELECT param_json->>'ldm_to_kg' AS ldm_factor
FROM tariff_rule
WHERE tenant_id = $1 
  AND carrier_id = $2
  AND rule_type = 'ldm_conversion';
```

### 5. Soft Delete Pattern

**Never hard DELETE:**
```sql
-- ❌ BAD: Permanently destroys data
DELETE FROM shipment WHERE id = $1;

-- ✅ GOOD: Mark as deleted
UPDATE shipment SET deleted_at = now() WHERE id = $1;

-- Queries automatically exclude soft-deleted
SELECT * FROM shipment 
WHERE deleted_at IS NULL
  AND carrier_id = $1;
```

**TypeORM implementation:**
```typescript
@Entity()
export class Shipment {
  @DeleteDateColumn()
  deleted_at: Date;
  
  // TypeORM automatically adds WHERE deleted_at IS NULL
}

// Soft delete
await shipmentRepo.softDelete(id);

// Hard delete (only for cleanup jobs)
await shipmentRepo.delete(id);

// Include soft-deleted in query
await shipmentRepo.find({ withDeleted: true });
```

### 6. Currency Handling

**Store amounts in original currency:**
```sql
CREATE TABLE shipment (
  -- ...
  currency CHAR(3) NOT NULL DEFAULT 'EUR', -- ISO 4217
  actual_total_amount DECIMAL(12,2) NOT NULL,
  
  -- NOT: actual_total_eur DECIMAL(12,2)
);

CREATE TABLE shipment_benchmark (
  -- ...
  expected_base_amount DECIMAL(10,2), -- In shipment.currency
  expected_diesel_amount DECIMAL(10,2),
  expected_toll_amount DECIMAL(10,2),
  expected_total_amount DECIMAL(10,2),
  
  -- Conversion metadata
  fx_rate_used NUMERIC(18,8),
  fx_rate_date DATE
);
```

**Conversion happens at report time:**
```typescript
// Load tenant's reporting currency
const reportCurrency = tenant.default_currency; // e.g., 'EUR'

// Convert each shipment
for (const benchmark of benchmarks) {
  if (benchmark.shipment.currency === reportCurrency) {
    benchmark.converted_amount = benchmark.expected_total_amount;
  } else {
    const fx = await fxService.getRate(
      benchmark.shipment.currency,
      reportCurrency,
      benchmark.shipment.date
    );
    benchmark.converted_amount = round(benchmark.expected_total_amount * fx.rate);
  }
}
```

## Migration Patterns

### Migration Naming

```
001_create_tenant_table.ts
002_create_user_table.ts
003_enable_rls_on_tenant.ts
004_add_currency_to_shipment.ts
```

### Migration Template

```typescript
import { MigrationInterface, QueryRunner } from 'typeorm';

export class CreateShipmentTable1704067200000 implements MigrationInterface {
  name = 'CreateShipmentTable1704067200000';

  public async up(queryRunner: QueryRunner): Promise<void> {
    // Create table
    await queryRunner.query(`
      CREATE TABLE shipment (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id UUID NOT NULL REFERENCES tenant(id),
        upload_id UUID NOT NULL REFERENCES upload(id),
        
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
    `);

    // Create indexes
    await queryRunner.query(`
      CREATE INDEX idx_shipment_tenant ON shipment(tenant_id);
      CREATE INDEX idx_shipment_upload ON shipment(upload_id);
      CREATE INDEX idx_shipment_carrier ON shipment(carrier_id);
      CREATE INDEX idx_shipment_date ON shipment(date);
      CREATE INDEX idx_shipment_deleted ON shipment(deleted_at) 
        WHERE deleted_at IS NULL;
    `);

    // Enable RLS
    await queryRunner.query(`
      ALTER TABLE shipment ENABLE ROW LEVEL SECURITY;
      
      CREATE POLICY tenant_isolation ON shipment
        USING (tenant_id = current_setting('app.current_tenant')::UUID);
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE shipment CASCADE;`);
  }
}
```

### Data Migration Pattern

```typescript
export class MigrateCurrencyField1704100000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // Add new column
    await queryRunner.query(`
      ALTER TABLE shipment 
      ADD COLUMN currency CHAR(3) DEFAULT 'EUR';
    `);
    
    // Migrate data (in batches for large tables)
    await queryRunner.query(`
      UPDATE shipment 
      SET currency = COALESCE(meta->>'currency', 'EUR')
      WHERE currency IS NULL;
    `);
    
    // Make NOT NULL after data is migrated
    await queryRunner.query(`
      ALTER TABLE shipment 
      ALTER COLUMN currency SET NOT NULL;
    `);
    
    // Remove old field from meta
    await queryRunner.query(`
      UPDATE shipment 
      SET meta = meta - 'currency';
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    // Restore old format
    await queryRunner.query(`
      UPDATE shipment 
      SET meta = meta || jsonb_build_object('currency', currency);
    `);
    
    await queryRunner.query(`
      ALTER TABLE shipment DROP COLUMN currency;
    `);
  }
}
```

## Index Strategy

### Standard Indexes

```sql
-- Primary key (automatic)
CREATE INDEX automatically via PRIMARY KEY

-- Tenant isolation (critical for RLS performance)
CREATE INDEX idx_<table>_tenant ON <table>(tenant_id);

-- Foreign keys (for JOINs)
CREATE INDEX idx_<table>_<fk> ON <table>(<fk_column>);

-- Date range queries
CREATE INDEX idx_<table>_date ON <table>(date_column);

-- Soft delete (partial index)
CREATE INDEX idx_<table>_deleted ON <table>(deleted_at) 
  WHERE deleted_at IS NULL;
```

### Composite Indexes

For common multi-column queries:
```sql
-- Zone lookup query: WHERE tenant_id = X AND carrier_id = Y AND country = Z AND plz_prefix = W
CREATE INDEX idx_zone_lookup ON tariff_zone_map(
  tenant_id, carrier_id, country, plz_prefix
);

-- Tariff lookup: WHERE tenant_id AND carrier_id AND lane_type AND zone AND valid_from <= date
CREATE INDEX idx_tariff_lookup ON tariff_table(
  tenant_id, carrier_id, lane_type, zone, valid_from DESC
);

-- Diesel lookup: WHERE tenant_id AND carrier_id AND valid_from <= date
CREATE INDEX idx_diesel_lookup ON diesel_floater(
  tenant_id, carrier_id, valid_from DESC
);
```

### JSONB Indexes

```sql
-- GIN index for full document search
CREATE INDEX idx_shipment_source_data ON shipment USING GIN (source_data);

-- Query: source_data @> '{"carrier": "DHL"}'
-- Or:    source_data->>'reference_number' = 'X'

-- Expression index for specific field
CREATE INDEX idx_shipment_ref ON shipment((source_data->>'reference_number'));
```

## Common Queries

### 1. Find Shipments with Benchmark

```typescript
const results = await db.query(`
  SELECT 
    s.id,
    s.date,
    s.carrier_id,
    c.name AS carrier_name,
    s.origin_zip,
    s.dest_zip,
    s.currency,
    s.actual_total_amount,
    b.expected_total_amount,
    b.delta_amount,
    b.delta_pct,
    b.classification
  FROM shipment s
  LEFT JOIN shipment_benchmark b ON s.id = b.shipment_id
  LEFT JOIN carrier c ON s.carrier_id = c.id
  WHERE s.tenant_id = $1
    AND s.upload_id = $2
    AND s.deleted_at IS NULL
  ORDER BY b.delta_amount DESC NULLS LAST
  LIMIT 100
`, [tenantId, uploadId]);
```

### 2. Calculate Zone for Shipment

```typescript
const zone = await db.query(`
  SELECT zone
  FROM tariff_zone_map
  WHERE tenant_id = $1
    AND carrier_id = $2
    AND country = $3
    AND (
      (plz_prefix IS NOT NULL AND $4 LIKE plz_prefix || '%')
      OR (pattern IS NOT NULL AND $4 ~ pattern)
    )
    AND valid_from <= $5
    AND (valid_until IS NULL OR valid_until >= $5)
  ORDER BY 
    prefix_len DESC NULLS LAST, -- Prefer longer prefix
    valid_from DESC
  LIMIT 1
`, [tenantId, carrierId, destCountry, destZip, shipmentDate]);
```

### 3. Find Applicable Tariff

```typescript
const tariff = await db.query(`
  SELECT 
    id,
    base_amount,
    currency,
    weight_min,
    weight_max
  FROM tariff_table
  WHERE tenant_id = $1
    AND carrier_id = $2
    AND lane_type = $3
    AND zone = $4
    AND $5 BETWEEN weight_min AND weight_max
    AND valid_from <= $6
    AND (valid_until IS NULL OR valid_until >= $6)
  ORDER BY valid_from DESC
  LIMIT 1
`, [tenantId, carrierId, laneType, zone, chargeableWeight, shipmentDate]);
```

### 4. Get Current Diesel Floater

```typescript
const diesel = await db.query(`
  SELECT pct, basis
  FROM diesel_floater
  WHERE tenant_id = $1
    AND carrier_id = $2
    AND valid_from <= $3
    AND (valid_until IS NULL OR valid_until >= $3)
  ORDER BY valid_from DESC
  LIMIT 1
`, [tenantId, carrierId, shipmentDate]);
```

### 5. Aggregate Report by Carrier

```typescript
const report = await db.query(`
  SELECT 
    c.name AS carrier,
    COUNT(*) AS shipment_count,
    SUM(s.actual_total_amount) AS actual_total,
    SUM(b.expected_total_amount) AS expected_total,
    SUM(b.delta_amount) AS total_delta,
    AVG(b.delta_pct) AS avg_delta_pct,
    SUM(CASE WHEN b.classification = 'drüber' THEN 1 ELSE 0 END) AS overpay_count
  FROM shipment s
  JOIN shipment_benchmark b ON s.id = b.shipment_id
  JOIN carrier c ON s.carrier_id = c.id
  WHERE s.tenant_id = $1
    AND s.upload_id = $2
    AND s.deleted_at IS NULL
  GROUP BY c.id, c.name
  ORDER BY total_delta DESC
`, [tenantId, uploadId]);
```

## Performance Optimization

### 1. Batch Inserts

```typescript
// ❌ BAD: N queries
for (const shipment of shipments) {
  await shipmentRepo.save(shipment);
}

// ✅ GOOD: 1 query
await shipmentRepo.insert(shipments);

// ✅ BEST: Batched chunks (avoid memory issues)
const BATCH_SIZE = 1000;
for (let i = 0; i < shipments.length; i += BATCH_SIZE) {
  const batch = shipments.slice(i, i + BATCH_SIZE);
  await shipmentRepo.insert(batch);
}
```

### 2. Parallel Queries

```typescript
// ❌ BAD: Sequential
const carrier = await carrierRepo.findOne({ id: carrierId });
const tariff = await tariffRepo.findOne({ carrier_id: carrierId });
const diesel = await dieselRepo.findOne({ carrier_id: carrierId });

// ✅ GOOD: Parallel
const [carrier, tariff, diesel] = await Promise.all([
  carrierRepo.findOne({ id: carrierId }),
  tariffRepo.findOne({ carrier_id: carrierId }),
  dieselRepo.findOne({ carrier_id: carrierId })
]);
```

### 3. Prepared Statements

```typescript
// TypeORM uses prepared statements automatically
// But for raw queries:
const stmt = await db.prepare(`
  SELECT * FROM shipment WHERE tenant_id = $1 AND date >= $2
`);

for (const tenantId of tenantIds) {
  const results = await stmt.execute([tenantId, startDate]);
  // ...
}

await stmt.deallocate();
```

### 4. Connection Pooling

```typescript
// ormconfig.ts
export default {
  type: 'postgres',
  host: process.env.DB_HOST,
  port: 5432,
  
  extra: {
    max: 20, // Max connections in pool
    min: 5,  // Min idle connections
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 2000
  }
};
```

## Testing Database Code

### 1. Test Database Setup

```typescript
// test/setup.ts
beforeAll(async () => {
  // Create test database
  await execSync('createdb freightwatch_test');
  
  // Run migrations
  await execSync('npm run migration:run');
});

afterAll(async () => {
  // Drop test database
  await execSync('dropdb freightwatch_test');
});
```

### 2. Transaction Rollback Pattern

```typescript
describe('ShipmentService', () => {
  let connection: Connection;
  let queryRunner: QueryRunner;
  
  beforeEach(async () => {
    connection = getConnection();
    queryRunner = connection.createQueryRunner();
    await queryRunner.startTransaction();
  });
  
  afterEach(async () => {
    // Rollback transaction (auto-cleanup)
    await queryRunner.rollbackTransaction();
    await queryRunner.release();
  });
  
  it('creates shipment', async () => {
    const shipment = await shipmentService.create(/* ... */);
    expect(shipment.id).toBeDefined();
    // No manual cleanup needed - transaction will rollback
  });
});
```

## References

- PostgreSQL 14 Documentation: https://www.postgresql.org/docs/14/
- Row-Level Security: https://www.postgresql.org/docs/14/ddl-rowsecurity.html
- JSONB Operators: https://www.postgresql.org/docs/14/functions-json.html
- TypeORM Migrations: https://typeorm.io/migrations