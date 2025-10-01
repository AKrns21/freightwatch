# Architecture Guide

This document describes FreightWatch's system architecture, component interactions, and data flow patterns.

## System Overview

FreightWatch is a multi-tenant B2B SaaS for freight cost analysis. Core workflow:

```
Upload → Parse → Normalize → Calculate Benchmark → Generate Report
```

### Architecture Principles

1. **PostgreSQL-only**: No MongoDB or other databases. JSONB for unstructured data.
2. **Tariff Engine as Core**: All cost calculations centralized in one service
3. **Multi-tenant from Day 1**: Row-Level Security (RLS) enforced on every query
4. **Multi-currency/Multi-country**: Never hardcode EUR or DE
5. **Deterministic Calculations**: Same input = same output (via `round()` util)

## System Components

```
┌──────────────────────────────────────────────────────────────┐
│                      Frontend (React)                        │
│  - File Upload  - Parsing Status  - Cost Analysis Report    │
└──────────────────┬───────────────────────────────────────────┘
                   │ REST API
┌──────────────────┴───────────────────────────────────────────┐
│                   API Gateway (NestJS)                       │
│  - Auth (JWT)  - RLS Context  - Rate Limiting               │
└──────────┬──────────────────┬────────────────────────────────┘
           │                  │
┌──────────▼──────────┐   ┌───────▼────────────────────────────┐
│  Parsing Service    │   │    Tariff Engine                   │
│  - CSV/Excel        │   │    - Zone Calculation              │
│  - PDF (regex)      │   │    - Base Cost Calculation         │
│  - Surcharge Parse  │   │    - Surcharge Application         │
│  - Normalization    │   │    - Overpay Detection            │
└──────────┬──────────┘   └────────┬───────────────────────────┘
           │                       │
           └───────────┬───────────┘
                       │
            ┌──────────▼──────────┐
            │  PostgreSQL 14+     │
            │  - RLS enabled      │
            │  - JSONB for raw    │
            └─────────────────────┘
```

### Component Responsibilities

**API Gateway (NestJS)**
- JWT authentication & authorization
- Tenant context injection via `TenantInterceptor`
- Request validation & rate limiting
- Global error handling

**Parsing Service**
- File upload handling (CSV, Excel, PDF)
- Format detection & validation
- Data extraction & normalization
- Carrier/service mapping
- Queue-based processing (BullMQ)

**Tariff Engine**
- Zone calculation (PLZ → Zone mapping)
- Chargeable weight calculation (from `tariff_rule`)
- Base cost lookup (zone + weight range + date)
- Diesel surcharge (interval-based via `diesel_floater`)
- Toll estimation/validation
- Currency conversion (via `fx_rate`)
- Overpay classification (±5% threshold)

**Report Service**
- Aggregated cost analysis
- Delta calculations (actual vs expected)
- Currency-normalized totals
- Export formats (PDF, Excel, JSON)

## Data Flow

### 1. Upload Pipeline

```typescript
POST /api/upload
  ↓
1. Calculate SHA256 hash
2. Check deduplication (tenant_id + file_hash)
3. Save file to storage
4. Create upload record (status='pending')
5. Enqueue parse job → BullMQ
6. Return upload_id to client
  ↓
Worker Process:
  ↓
7. Detect file format (CSV/Excel/PDF)
8. Extract shipments via parser
9. Map carrier names → carrier_id
10. Save shipments to DB (batch insert)
11. Update upload status='parsed'
12. Trigger benchmark calculation
```

### 2. Benchmark Calculation Flow

```typescript
Trigger: After shipments are parsed
  ↓
For each shipment (parallel, max 5 concurrent):
  ↓
1. Determine lane type
   - domestic_de, domestic_at, de_to_ch, at_to_de, etc.
  ↓
2. Calculate zone
   - Query tariff_zone_map with tenant_id, carrier_id, country, dest_zip
   - Use prefix_len or pattern matching
  ↓
3. Calculate chargeable weight
   - Load tariff_rule (e.g., ldm_to_kg conversion)
   - Compare weight_kg vs chargeable_weight
   - Use MAX(weight_kg, ldm * ldm_to_kg)
  ↓
4. Find applicable tariff
   - Query tariff_table with:
     * tenant_id, carrier_id
     * lane_type
     * zone (calculated above)
     * chargeable_weight (in weight_min/weight_max range)
     * shipment.date (between valid_from and valid_until)
  ↓
5. Apply currency conversion
   - If tariff.currency ≠ shipment.currency
   - Query fx_rate for rate_date = shipment.date
   - Convert base_amount to shipment.currency
  ↓
6. Calculate diesel surcharge
   - Query diesel_floater for valid_from ≤ shipment.date
   - Apply pct to correct basis:
     * 'base': base_amount * pct
     * 'base_plus_toll': (base_amount + toll) * pct
     * 'total': total_amount * pct
  ↓
7. Add toll
   - Prefer shipment.toll_amount (if parsed from invoice)
   - Else: estimate via weight-based heuristic (3.5t threshold)
   - Note: Fallback is rough estimate, mark with 'estimated_heuristic'
  ↓
8. Calculate expected total
   expected_total = round(base + diesel + toll)
  ↓
9. Calculate delta
   delta = round(shipment.actual_total_amount - expected_total)
   delta_pct = (delta / expected_total) * 100
  ↓
10. Classify overpay
    - 'unter': delta_pct < -5%
    - 'im_markt': -5% ≤ delta_pct ≤ 5%
    - 'drüber': delta_pct > 5%
  ↓
11. Convert to tenant reporting currency
    - Load tenant.default_currency (e.g., EUR)
    - Convert expected_total_amount & actual_total_amount
  ↓
12. Save shipment_benchmark record
    - Store all breakdown details in cost_breakdown JSONB
    - Include tariff_table_id, diesel_pct_used, fx_rate_used
```

### 3. Report Generation

```typescript
GET /api/reports/:upload_id
  ↓
1. Load shipment_benchmark records (with RLS)
2. Aggregate by classification ('unter', 'im_markt', 'drüber')
3. Calculate totals in tenant.default_currency
4. Identify top overpay shipments (sorted by delta DESC)
5. Group by carrier, lane, zone for drill-down
6. Return JSON or trigger PDF/Excel export
```

## Module Structure

```
backend/src/modules/
├── auth/
│   ├── jwt.strategy.ts          # JWT validation
│   ├── tenant.interceptor.ts    # RLS context injection
│   └── guards/
├── upload/
│   ├── upload.controller.ts     # File upload endpoint
│   ├── upload.service.ts        # Hash, dedupe, storage
│   └── upload.processor.ts      # BullMQ worker
├── parsing/
│   ├── parsers/
│   │   ├── csv-parser.ts        # CSV/Excel parsing
│   │   ├── pdf-parser.ts        # Regex-based PDF extraction
│   │   └── parser-factory.ts    # Format detection
│   ├── mappers/
│   │   ├── carrier-mapper.ts    # Name → carrier_id
│   │   └── service-mapper.ts    # Alias → service_code
│   └── parsing.service.ts
├── tariff/
│   ├── engines/
│   │   ├── tariff-engine.ts     # Main calculation orchestrator
│   │   ├── zone-calculator.ts   # PLZ → Zone mapping
│   │   ├── weight-calculator.ts # Chargeable weight
│   │   └── fx-service.ts        # Currency conversion
│   ├── services/
│   │   ├── diesel-service.ts    # Diesel floater lookup
│   │   └── toll-service.ts      # Toll estimation
│   └── benchmark.service.ts     # Orchestrates full calculation
└── report/
    ├── report.controller.ts     # Report API
    ├── report.service.ts        # Aggregation logic
    └── exporters/
        ├── pdf-exporter.ts
        └── excel-exporter.ts
```

## API Design

### RESTful Endpoints

All endpoints require JWT token in `Authorization: Bearer <token>` header.

**Upload**
```
POST   /api/upload              # Upload file
GET    /api/upload/:id          # Get upload status
GET    /api/upload/:id/shipments # List parsed shipments
DELETE /api/upload/:id          # Soft delete
```

**Tariff Management**
```
GET    /api/tariffs             # List tariff tables
POST   /api/tariffs/import      # Import tariff from CSV/Excel
GET    /api/tariffs/:id         # Get tariff details
PUT    /api/tariffs/:id         # Update tariff validity
DELETE /api/tariffs/:id         # Soft delete
```

**Reports**
```
GET    /api/reports/:upload_id  # Get cost analysis report
GET    /api/reports/:upload_id/export?format=pdf # Export report
GET    /api/reports/summary     # Aggregate across uploads
```

**Diesel Floaters**
```
GET    /api/diesel-floaters     # List diesel percentages
POST   /api/diesel-floaters     # Add new interval
PUT    /api/diesel-floaters/:id # Update
```

### Response Formats

**Standard Success**
```json
{
  "success": true,
  "data": { ... },
  "meta": {
    "timestamp": "2025-01-07T12:00:00Z",
    "tenant_id": "uuid"
  }
}
```

**Standard Error**
```json
{
  "success": false,
  "error": {
    "code": "PARSING_FAILED",
    "message": "Could not parse row 42: invalid date format",
    "details": { ... }
  }
}
```

## Critical Architecture Decisions

### 1. Why PostgreSQL-Only?

- **Simplicity**: Single database for all data types
- **ACID**: Transactional guarantees for cost calculations
- **RLS**: Native multi-tenancy support
- **JSONB**: Flexible storage for raw parsed data
- **Performance**: Indexes on JSONB fields (GIN indexes)

**No MongoDB because:**
- Added complexity (2 databases to sync)
- No native RLS → manual tenant filtering prone to bugs
- Eventual consistency issues for financial data

### 2. Why Interval-Based Diesel?

Old approach: `diesel_floater.month = '2024-01'` → breaks on mid-month changes

New approach: `valid_from` / `valid_until` → supports any change frequency

```sql
-- Query for correct diesel% at shipment date
SELECT pct, basis 
FROM diesel_floater
WHERE tenant_id = $1 
  AND carrier_id = $2
  AND valid_from <= $3
ORDER BY valid_from DESC
LIMIT 1;
```

### 3. Why Tariff Rules Instead of Hardcoded Logic?

**Bad:**
```typescript
const ldmToKg = 1850; // breaks for carriers with different ratios
const minPalletWeight = 300; // doesn't exist for all carriers
```

**Good:**
```typescript
const rule = await tariffRuleRepo.findOne({
  tenant_id: tenantId,
  carrier_id: carrierId,
  rule_type: 'ldm_conversion'
});
const ldmToKg = rule?.param_json.ldm_to_kg ?? null;
if (!ldmToKg) {
  throw new Error('Missing ldm_to_kg rule for carrier');
}
```

This allows per-tenant and per-carrier customization without code changes.

### 4. Why Normalize Service Levels?

Carriers use inconsistent naming:
- DHL: "Premium", "Express", "Standard"
- FedEx: "Priority", "Economy", "International Priority"
- Gebrüder Weiss: "24h", "Next Day", "Normal"

FreightWatch normalizes to:
- `STANDARD`, `EXPRESS`, `ECONOMY`, `NEXT_DAY`, `SAME_DAY`, `PREMIUM`

Mapping via `service_alias` table:
- Global defaults (NULL tenant_id)
- Carrier-specific overrides
- Tenant-specific customization

Benefits:
- Consistent reporting across carriers
- Easier tariff matching (service_level filter)
- User-friendly report grouping

## Scalability Considerations

**Current (MVP):**
- Single server (API + Worker)
- PostgreSQL with read replicas
- Redis for queue + cache
- Target: 10k shipments/upload, <30s processing

**Future (Post-MVP):**
- Horizontal scaling via Kubernetes
- Separate worker pools per tenant
- Partitioned tables (by tenant_id + date)
- CDN for report exports
- Target: 100k+ shipments/upload

## Security Architecture

**Authentication:**
- JWT tokens (15min expiry)
- Refresh tokens (7 days)
- Revocation via Redis blacklist

**Authorization:**
- Role-based: `admin`, `user`, `readonly`
- Resource ownership via RLS
- API rate limiting (100 req/min per tenant)

**Data Protection:**
- TLS 1.3 for all connections
- Passwords hashed with bcrypt (cost=12)
- Sensitive fields encrypted at rest (planned for Post-MVP)
- Audit log for all data changes

**RLS Enforcement:**
Every query runs with `app.current_tenant` set:
```typescript
await db.query('SET app.current_tenant = $1', [tenantId]);
```

Policies block cross-tenant reads:
```sql
CREATE POLICY tenant_isolation ON shipment
  USING (tenant_id = current_setting('app.current_tenant')::UUID);
```

## Deployment Architecture

**Development:**
```
docker-compose up -d
├── api (port 3000)
├── postgres (port 5432)
├── redis (port 6379)
└── worker (BullMQ consumer)
```

**Production (MVP):**
```
AWS/Azure/GCP
├── ECS/AKS (API + Worker containers)
├── RDS PostgreSQL (Multi-AZ)
├── ElastiCache Redis
├── S3/Blob Storage (uploaded files)
└── CloudWatch/AppInsights (logs)
```

**CI/CD:**
- GitHub Actions
- Test → Build → Deploy on `main` push
- Automated migrations via `npm run migration:run`
- Blue-green deployment (zero downtime)

## Observability

**Logging:**
- Structured JSON logs (Winston)
- Log levels: `error`, `warn`, `info`, `debug`
- Context: `tenant_id`, `user_id`, `upload_id`, `request_id`

**Metrics:**
- Upload success/failure rates
- Parsing coverage (% of rows successfully parsed)
- Benchmark accuracy (% within ±5%)
- API response times (p50, p95, p99)
- Worker queue depth

**Alerts:**
- Parsing failure rate >10%
- Benchmark calculation timeout
- Database connection pool exhaustion
- RLS policy violation attempts

## References

- For database schema details, see `@docs/claude/database-patterns.md`
- For tariff engine logic, see `@docs/claude/business-logic.md`
- For coding conventions, see `@docs/claude/coding-standards.md`