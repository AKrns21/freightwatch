# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@docs/claude/architecture.md
@docs/claude/database-patterns.md
@docs/claude/business-logic.md
@docs/claude/project-workflow.md
@docs/claude/coding-standards.md
@docs/claude/testing-standards.md

## Project Context

FreightWatch is a multi-tenant B2B SaaS system for freight cost analysis. It parses invoices from carriers (CSV/Excel/PDF), calculates expected costs using tariff tables, and identifies overpayment opportunities. The system supports multiple currencies, countries, and carriers with strict tenant isolation via PostgreSQL Row Level Security (RLS).

**Stack:**
- **Backend:** NestJS + TypeScript + PostgreSQL 14+ + Redis + TypeORM + Bull (queues)
- **Frontend:** React 19 + TypeScript + Vite + TailwindCSS + React Router
- **LLM Integration:** Anthropic Claude 3.5 Sonnet (carrier/service detection)
- **Infrastructure:** Docker Compose (dev), PostgreSQL 14, Redis 7

## Quick Commands

### Initial Setup
```bash
# 1. Start infrastructure (PostgreSQL + Redis)
docker compose up -d

# 2. Backend setup
cd backend
npm install
cp .env.example .env
# Edit .env with your configuration
npm run migration:run    # Run database migrations
npm run start:dev        # Start backend dev server (port 3000)

# 3. Frontend setup (in separate terminal)
cd frontend
npm install
cp .env.example .env
# Edit .env (set VITE_API_URL=http://localhost:4000)
npm run dev             # Start frontend dev server (port 5173)
```

### Supabase Database Connection

**IMPORTANT:** The `postgres` superuser password cannot be set via the Supabase Dashboard for this project (known Supavisor bug on projects created 2026-03-16+). A dedicated `freightwatch_app` role was created instead.

**Connection method:** Session Mode Pooler (Supavisor) — required because direct connections (`db.*.supabase.co:5432`) have IPv4 disabled.

```
Host:     aws-1-eu-west-1.pooler.supabase.com
Port:     5432
User:     freightwatch_app.jvucxzrsiqzcaojnpazu
Database: postgres
SSL:      required (DB_SSL=true in .env)
```

**If connection issues recur:**
1. Do NOT reset the `postgres` password in the Dashboard — it doesn't propagate to Supavisor
2. Use the Supabase Management API to manage the `freightwatch_app` role directly:
   ```bash
   RAW_TOKEN=$(security find-generic-password -s "Supabase CLI" -w)
   ACCESS_TOKEN=$(echo "${RAW_TOKEN#go-keyring-base64:}" | base64 -d)
   python3 -c "import json; print(json.dumps({'query': \"ALTER ROLE freightwatch_app WITH PASSWORD 'NewPassword'\"}))" | \
     curl -s -X POST "https://api.supabase.com/v1/projects/jvucxzrsiqzcaojnpazu/database/query" \
       -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" -d @-
   ```
3. The Supabase CLI (`supabase inspect db ...`) connects via `cli_login_` mechanism and always works

### Backend Development (run from `backend/` directory)
- Start dev server: `npm run start:dev` (watches for changes)
- Build for production: `npm run build`
- Format code: `npm run format`
- Lint code: `npm run lint`

### Frontend Development (run from `frontend/` directory)
- Start dev server: `npm run dev` (Vite dev server on port 5173)
- Build for production: `npm run build`
- Preview production build: `npm run preview`
- Lint code: `npm run lint`

### Testing (run from `backend/` directory)
- Unit tests: `npm run test`
- Watch mode: `npm run test:watch`
- Integration tests: `npm run test:e2e`
- Coverage report: `npm run test:cov`
- MECU validation: `npm run test:integration`

### Database (run from `backend/` directory)
- Generate migration: `npm run typeorm migration:generate -- -n MigrationName`
- Revert migration: `npm run typeorm migration:revert`
- Check schema: `npm run typeorm schema:log`

### Docker
- Start services: `docker compose up -d`
- View logs: `docker compose logs -f postgres`
- Stop services: `docker compose down`
- Clean volumes: `docker compose down -v`

**Note:** Always use `npm` (not yarn) for consistency. All backend commands run from `backend/`, frontend from `frontend/`.

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
// LDM conversion factors stored in carrier-specific configuration
const ldmToKg = carrier.ldm_to_kg_factor || 1850; // ✅ with logged fallback
// NOT: const ldmToKg = 1850; // ❌ hardcoded without context
```
**If fallback needed:** Document in code comments + log warning with `logger.warn()`.
**MVP Exception:** Zone fallbacks (1 for DE, 3 for international) are temporarily allowed during MVP phase with extensive logging. See tariff-engine.service.ts for documentation.

### 5. TypeScript Guidelines
**Note:** TypeScript strict mode is currently **disabled** for MVP development speed. However, new code should still follow best practices:

```typescript
// ✅ GOOD: Explicit return types for all public methods
async calculateCost(s: Shipment): Promise<BenchmarkResult> { }

// ❌ BAD: No return type
async calculateCost(s: Shipment) { }

// ✅ GOOD: Explicit parameter types
function round(value: number): number { }

// ❌ BAD: Avoid 'any' when possible
function process(data: any): any { }
```

**Post-MVP:** Strict mode will be re-enabled gradually. See `@docs/claude/coding-standards.md` for full guidelines.

## Repository Structure

```
Repository/
├── backend/                 # NestJS API Backend
│   ├── src/
│   │   ├── modules/
│   │   │   ├── auth/       # JWT + Tenant Interceptor (RLS)
│   │   │   ├── upload/     # File handling + Bull Queue
│   │   │   ├── parsing/    # CSV/PDF parsers + LLM integration
│   │   │   ├── tariff/     # Core calculation engine
│   │   │   ├── project/    # Project management (Phase 4.1)
│   │   │   ├── report/     # Report generation
│   │   │   └── invoice/    # Invoice entities
│   │   ├── database/
│   │   │   ├── migrations/ # SQL migrations (001_, 002_, ...)
│   │   │   └── seeds/      # Test/dev seed data
│   │   ├── utils/
│   │   │   ├── round.ts    # Monetary rounding (CRITICAL)
│   │   │   ├── date-parser.ts  # EU date formats (dd.mm.yyyy)
│   │   │   └── hash.ts     # SHA256 file hashing
│   │   └── types/          # Shared TypeScript interfaces
│   ├── test/
│   │   ├── fixtures/mecu/  # Real customer test data
│   │   └── integration/    # E2E tests
│   ├── package.json
│   ├── tsconfig.json
│   └── .env.example
├── frontend/                # React + Vite Frontend
│   ├── src/
│   │   ├── components/     # React components
│   │   ├── pages/          # Page components
│   │   ├── hooks/          # Custom React hooks
│   │   ├── services/       # API client services
│   │   ├── types/          # TypeScript types
│   │   └── utils/          # Helper functions
│   ├── public/             # Static assets
│   ├── package.json
│   ├── vite.config.ts
│   └── .env.example
├── docs/                    # Documentation
│   └── claude/             # Claude Code documentation
│       ├── architecture.md
│       ├── database-patterns.md
│       ├── business-logic.md
│       ├── project-workflow.md
│       ├── coding-standards.md
│       └── testing-standards.md
├── docker-compose.yml       # PostgreSQL + Redis
├── CLAUDE.md               # This file
└── README.md               # Project overview
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
2. Calculate zone (carrier-specific PLZ mapping from tariff_zone_map)
3. Calculate chargeable weight (MAX of actual weight vs LDM-based volumetric weight)
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
- **project**: Project management (Phase 4.1) with workflow tracking
- **upload**: File uploads linked to projects with parse metadata
- **shipment**: Core entity (tenant_id, currency, amounts, zone, weight, project_id)
- **tariff_table**: Tariffs (carrier, lane, zone, weight range, valid_from/until)
- **tariff_zone_map**: PLZ to zone mappings (carrier-specific, temporal validity)
- **diesel_floater**: Time-based diesel% (valid_from/until, basis='base'|'base_plus_toll')
- **fx_rate**: Historical exchange rates (rate_date, from_ccy, to_ccy)
- **shipment_benchmark**: Expected costs + delta + classification
- **parsing_template**: LLM-powered parsing templates per carrier
- **manual_mapping**: Human-reviewed carrier/service mappings
- **consultant_note**: Quality issues and observations per project

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
✅ **Fix:** Load from carrier configuration or use documented fallback with warning

See @docs/claude/common-pitfalls.md for complete list.

## Code Style

- **Naming:** camelCase (TS), snake_case (DB), kebab-case (files)
- **Imports:** Absolute paths via `@/` alias
- **Error Handling:** NestJS exceptions (`BadRequestException`, `NotFoundException`)
- **Logging:** Structured JSON with `logger.info/warn/error({ event, ...context })`
- **Commits:** Conventional commits (`feat:`, `fix:`, `test:`, `docs:`)

See @docs/claude/coding-standards.md for details.

---

**Last Updated:** 2025-10-14
**Version:** 1.2 (MVP + Phase 4.1 + Frontend Integration)