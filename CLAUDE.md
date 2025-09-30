# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@docs/claude/architecture.md
@docs/claude/database-patterns.md
@docs/claude/business-logic.md
@docs/claude/coding-standards.md
@docs/claude/testing-standards.md
@docs/claude/common-pitfalls.md

## Project Context

FreightWatch is a multi-tenant B2B SaaS system for freight cost analysis. It parses invoices from carriers (CSV/Excel/PDF), calculates expected costs using tariff tables, and identifies overpayment opportunities. The system supports multiple currencies, countries, and carriers with strict tenant isolation via PostgreSQL Row Level Security (RLS).

**Stack:** NestJS + TypeScript + PostgreSQL 14+ + Redis + TypeORM

## Quick Commands

### Development
- Start dev server: `npm run start:dev`
- Build for production: `npm run build`
- Run migrations: `npm run migration:run`
- Seed dev data: `npm run seed:dev`

### Testing
- Unit tests: `npm run test`
- Integration tests: `npm run test:e2e`
- Coverage report: `npm run test:cov`
- MECU validation: `npm run test:mecu`

### Database
- Generate migration: `npm run typeorm migration:generate -- -n MigrationName`
- Revert migration: `npm run typeorm migration:revert`
- Check schema: `npm run typeorm schema:log`

### Docker
- Start services: `docker-compose up -d`
- View logs: `docker-compose logs -f api`
- Stop services: `docker-compose down`

**Note:** Always use `npm` (not yarn) for consistency.

## CRITICAL RULES ⚠️

### 1. Row Level Security (RLS)
**Every database query MUST run within tenant context.**
```typescript
await db.setTenantContext(tenantId); // REQUIRED before any query
const data = await repo.find();
await db.resetTenantContext();
```
TenantInterceptor handles this globally for HTTP requests. Test cross-tenant isolation with 2+ tenants.

### 2. Monetary Calculations
**ALWAYS use `round()` from `src/utils/round.ts`:**
```typescript
import { round } from '@/utils/round';
const total = round(base + diesel + toll); // ✅
// NOT: const total = base + diesel + toll; // ❌ floating point errors
```

### 3. Currency Agnostic
**NEVER hardcode EUR.** Store amounts in original currency, convert only for reporting.
```typescript
return `${amount} ${shipment.currency}`; // ✅
// NOT: return `${amount} EUR`; // ❌
```

### 4. No Magic Numbers
**Business rules from database, NOT hardcoded:**
```typescript
const rule = await repo.findOne({ rule_type: 'ldm_conversion' });
const ldmToKg = rule?.param_json.ldm_to_kg; // ✅
// NOT: const ldmToKg = 1850; // ❌
```
If fallback needed: **Log warning** with `logger.warn()`.

### 5. TypeScript Strict
**Explicit return types for all public methods:**
```typescript
async calculateCost(s: Shipment): Promise<BenchmarkResult> { } // ✅
// NOT: async calculateCost(s: Shipment) { } // ❌
```

## Project Structure

```
backend/
├── src/
│   ├── modules/
│   │   ├── auth/           # JWT + Tenant Interceptor (RLS)
│   │   ├── upload/         # File handling + Queue
│   │   ├── parsing/        # CSV/PDF parsers
│   │   ├── tariff/         # Core calculation engine
│   │   ├── report/         # Report generation
│   │   └── fleet/          # Fleet analytics
│   ├── database/
│   │   ├── migrations/     # SQL migrations (001_, 002_, ...)
│   │   └── seeds/          # Test/dev seed data
│   ├── utils/
│   │   ├── round.ts        # Monetary rounding (CRITICAL)
│   │   ├── date-parser.ts  # EU date formats (dd.mm.yyyy)
│   │   └── hash.ts         # SHA256 file hashing
│   └── types/              # Shared TypeScript interfaces
└── test/
    ├── fixtures/mecu/      # Real customer test data
    └── integration/        # E2E tests
```

## Core Data Flow

**Upload Pipeline:**
```
1. Upload file → Hash (SHA256)
2. Check deduplication (file_hash + tenant_id)
3. Save to storage → Create upload record (status='pending')
4. Enqueue parse job
5. Worker: Parse → Map carriers → Save shipments
6. Calculate benchmarks (parallel, 5 concurrent)
7. Update status='parsed'
```

**Tariff Calculation:**
```
1. Determine lane type (domestic_de, de_to_ch, ...)
2. Calculate zone (carrier-specific PLZ mapping)
3. Calculate chargeable weight (from tariff_rule table)
4. Find tariff (zone + weight range + date + valid_from/until)
5. FX conversion (if tariff.currency ≠ shipment.currency)
6. Add diesel surcharge (from diesel_floater with basis)
7. Add toll (prefer shipment.toll_amount, else estimate)
8. Calculate delta = actual - expected
9. Classify: 'unter' / 'im_markt' / 'drüber' (±5% threshold)
10. Convert to tenant reporting currency & save benchmark
```

## Critical Tables

- **tenant**: Settings (currency, default_diesel_floater, data_retention_days)
- **shipment**: Core entity (tenant_id, currency, amounts, zone, weight)
- **tariff_table**: Tariffs (carrier, lane, zone, weight range, valid_from/until)
- **diesel_floater**: Time-based diesel% (valid_from/until, basis='base'|'base_plus_toll')
- **tariff_rule**: Business rules (ldm_conversion, min_pallet_weight) - **NO defaults!**
- **fx_rate**: Historical exchange rates (rate_date, from_ccy, to_ccy)
- **shipment_benchmark**: Expected costs + delta + classification

**All tenant-scoped tables have RLS enabled.**

## Testing Standards

**Unit Tests:**
- Mock external dependencies (ZoneCalculator, FXService)
- Test pure functions (round, date parsing)
- Known input/output pairs from MECU data

**Integration Tests:**
- Use MECU fixtures: `test/fixtures/mecu/sample.csv`
- Test full flow: Upload → Parse → Benchmark → Report
- Verify RLS isolation with 2+ tenants
- Check >50% overpay detection rate (MECU benchmark)

**Key Metrics:**
- Parsing coverage ≥90%
- Tariff plausibility ≥85% (expected vs actual within ±5%)
- Report generation <30s for 10k shipments

## Common Pitfalls

❌ **Forgetting to round:** `const total = base + diesel;` → `348.74999999998`  
✅ **Fix:** `const total = round(base + diesel);`

❌ **Missing tenant context in tests:** `await repo.find();` → returns nothing  
✅ **Fix:** `await db.setTenantContext(tenantId);` before query

❌ **Hardcoded EUR:** `return '€' + amount;`  
✅ **Fix:** `return shipment.currency + ' ' + amount;`

❌ **Magic numbers:** `const minWeight = pallets * 300;`  
✅ **Fix:** Load from `tariff_rule` table

See @docs/claude/common-pitfalls.md for complete list.

## Code Style

- **Naming:** camelCase (TS), snake_case (DB), kebab-case (files)
- **Imports:** Absolute paths via `@/` alias
- **Error Handling:** NestJS exceptions (`BadRequestException`, `NotFoundException`)
- **Logging:** Structured JSON with `logger.info/warn/error({ event, ...context })`
- **Commits:** Conventional commits (`feat:`, `fix:`, `test:`, `docs:`)

See @docs/claude/coding-standards.md for details.

---

**Last Updated:** 2025-01-07  
**Version:** 1.0 (MVP)