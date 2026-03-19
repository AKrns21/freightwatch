# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Context

FreightWatch is a multi-tenant B2B SaaS system for freight cost analysis. It parses invoices from carriers (CSV/Excel/PDF), calculates expected costs using tariff tables, and identifies overpayment opportunities. The system supports multiple currencies, countries, and carriers with strict tenant isolation via PostgreSQL Row Level Security (RLS).

**Stack:**
- **Backend:** Python 3.11 + FastAPI + SQLAlchemy 2.0 async + Alembic
- **Frontend:** React 19 + TypeScript + Vite + TailwindCSS + React Router
- **LLM Integration:** Anthropic Claude (carrier/service detection, PDF Vision OCR)
- **Database:** Supabase PostgreSQL (hosted) — schema unchanged from NestJS era
- **Infrastructure:** Docker Compose (dev, PostgreSQL only — no Redis)


## Quick Commands

### Initial Setup
```bash
# 1. Start infrastructure (PostgreSQL only)
docker compose up -d

# 2. Backend setup
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # Edit with your Supabase credentials
alembic upgrade head             # Run database migrations
uvicorn main:app --reload --port 4000   # Start backend dev server

# 3. Frontend setup (in separate terminal)
cd frontend
npm install
cp .env.example .env             # Set VITE_API_URL=http://localhost:4000
npm run dev                      # Start frontend dev server (port 5173)
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

### Backend Development (run from `backend/` with `.venv` active)
- Start dev server: `uvicorn main:app --reload --port 4000`
- Lint: `ruff check .`
- Format: `ruff format .`
- Type check: `mypy .`

### Frontend Development (run from `frontend/` directory)
- Start dev server: `npm run dev` (Vite dev server on port 5173)
- Build for production: `npm run build`
- Lint: `npm run lint`

### Testing (run from `backend/` with `.venv` active)
- All tests: `pytest`
- With coverage: `pytest --cov=app --cov-report=html`
- Specific module: `pytest tests/unit/test_tariff_engine.py`
- Integration tests: `pytest tests/integration/ -v`

### Database (run from `backend/` with `.venv` active)
- Generate migration: `alembic revision --autogenerate -m "description"`
- Apply migrations: `alembic upgrade head`
- Revert one step: `alembic downgrade -1`
- Check current: `alembic current`

### Docker
- Start services: `docker compose up -d`
- View logs: `docker compose logs -f postgres`
- Stop services: `docker compose down`
- Clean volumes: `docker compose down -v`

## CRITICAL RULES ⚠️

### 1. Row Level Security (RLS)
**Every database query MUST run within tenant context.**
```python
# Set tenant context before ANY query
await db.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": str(tenant_id)})
result = await db.execute(select(Shipment))
# Context resets automatically at transaction end (SET LOCAL)
```
A FastAPI dependency handles this globally for HTTP requests. Test cross-tenant isolation with 2+ tenants.

### 2. Monetary Calculations
**ALWAYS use `round_monetary()` from `app/utils/round.py`:**
```python
from app.utils.round import round_monetary
total = round_monetary(base + diesel + toll)  # ✅
# NOT: total = base + diesel + toll  # ❌ floating point errors
```

### 3. Currency Agnostic
**NEVER hardcode EUR.** Store amounts in original currency, convert only for reporting.
```python
return f"{amount} {shipment.currency}"  # ✅
# NOT: return f"{amount} EUR"  # ❌
```

### 4. No Magic Numbers
**Business rules from database, NOT hardcoded:**
```python
# LDM conversion factors stored in carrier-specific tariff_rule rows
rule = await get_tariff_rule(db, tenant_id, carrier_id, "ldm_conversion")
ldm_to_kg = rule.param_json["ldm_to_kg"] if rule else None
if ldm_to_kg is None:
    logger.warning("missing_ldm_rule", tenant_id=tenant_id, carrier_id=carrier_id)
    # fail or use documented fallback — never silently guess
```
**If fallback needed:** Document in comments + `logger.warning()`.

### 5. Python Style
```python
# ✅ GOOD: Explicit return types, Pydantic models for I/O
async def calculate_benchmark(shipment: Shipment) -> BenchmarkResult: ...

# ✅ GOOD: snake_case everywhere in Python; DB columns are also snake_case
expected_total_amount: Decimal

# ✅ GOOD: camelCase JSON responses (for frontend compatibility)
model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

# ❌ BAD: bare except, silent failures
try:
    zone = calculate_zone(...)
except Exception:
    zone = 1  # never guess
```

Use `ruff` (linting + formatting) and `mypy --strict` for new modules.

## Repository Structure

```
Repository/
├── backend/                     # FastAPI Backend (Python)
│   ├── main.py                  # FastAPI app entry point
│   ├── pyproject.toml           # Dependencies + tool config
│   ├── app/
│   │   ├── routers/             # FastAPI routers (one per domain)
│   │   │   ├── auth.py
│   │   │   ├── upload.py
│   │   │   ├── tariff.py
│   │   │   ├── report.py
│   │   │   └── project.py
│   │   ├── services/            # Business logic (pure functions + DB calls)
│   │   │   ├── tariff_engine.py
│   │   │   ├── zone_calculator.py
│   │   │   ├── benchmark.py
│   │   │   ├── fx_service.py
│   │   │   └── llm_parser.py
│   │   ├── models/              # SQLAlchemy ORM models
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── db/
│   │   │   └── session.py       # Async engine, get_db dependency
│   │   └── utils/
│   │       ├── round.py         # round_monetary() — CRITICAL
│   │       ├── date_parser.py   # EU date formats (dd.mm.yyyy)
│   │       └── hash.py          # SHA256 file hashing
│   ├── alembic/                 # Database migrations
│   │   └── versions/
│   └── tests/
│       ├── unit/                # Pure logic tests (no DB)
│       ├── integration/         # DB tests (real Supabase or local PG)
│       └── fixtures/mecu/       # Real customer test data
├── frontend/                    # React + Vite Frontend
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   ├── services/            # API client
│   │   └── types/
│   ├── package.json
│   └── vite.config.ts
├── docs/                        # Documentation
│   ├── ARCHITECTURE.md          # Full platform architecture (authoritative)
│   └── REFACTORING_PYTHON_MIGRATION.md
├── data/                        # Tariff/invoice JSON fixtures
├── supabase/                    # Supabase config + SQL migrations
├── docker-compose.yml           # PostgreSQL only (no Redis)
├── CLAUDE.md                    # This file
└── README.md
```

## Core Data Flow

**Upload Pipeline:**
```
1. Upload file → Hash (SHA256)
2. Check deduplication (file_hash + tenant_id)
3. Save to storage → Create upload record (status='pending')
4. FastAPI BackgroundTask: Parse → Map carriers → Save shipments
5. Calculate benchmarks (asyncio.gather with semaphore, max 5 concurrent)
6. Update status='parsed'
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
8. delta = actual - expected
9. Classify: 'unter' / 'im_markt' / 'drüber' (±5% threshold)
10. Convert to tenant reporting currency & save benchmark
```

## Critical Tables

- **tenant**: Settings (currency, default_diesel_floater, data_retention_days)
- **project**: Project management with workflow tracking
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

**Unit Tests (no DB required):**
```python
# tests/unit/test_round.py
def test_round_monetary_two_decimals():
    assert round_monetary(Decimal("10.126")) == Decimal("10.13")

# Mock DB dependencies with pytest fixtures / unittest.mock
```

**Integration Tests (real DB, transaction rollback):**
```python
@pytest.fixture
async def db_session(engine):
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": TEST_TENANT_ID})
        yield conn
        await conn.rollback()  # auto-cleanup
```

**RLS Isolation (mandatory for every tenant-scoped table):**
- Create data as tenant_1, switch to tenant_2, assert not visible
- Test patterns follow the fixtures in `tests/integration/`

**Key Metrics:**
- Parsing coverage ≥90%
- Tariff match rate ≥85%
- Report generation <30s for 10k shipments

## Common Pitfalls

❌ **Forgetting to round:** `total = base + diesel` → `Decimal('348.74999999998')`
✅ **Fix:** `total = round_monetary(base + diesel)`

❌ **Missing tenant context:** `await db.execute(select(Shipment))` → returns nothing (RLS blocks all)
✅ **Fix:** `SET LOCAL app.current_tenant = :tid` before query

❌ **Hardcoded EUR:** `return f"€{amount}"`
✅ **Fix:** `return f"{shipment.currency} {amount}"`

❌ **Magic numbers:** `min_weight = pallets * 300`
✅ **Fix:** Load from `tariff_rule` table with logged fallback

❌ **snake_case JSON to frontend:** `{"expected_total_amount": 100}` — frontend expects camelCase
✅ **Fix:** Use `model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)` in Pydantic schemas

## Code Style

- **Naming:** snake_case (Python + DB), PascalCase (classes), kebab-case (files optional)
- **Imports:** absolute from `app.` package root
- **Error Handling:** `HTTPException(status_code=..., detail=...)` for API errors
- **Logging:** structlog with `logger.info("event_name", key=value, ...)`
- **Commits:** Conventional commits (`feat:`, `fix:`, `test:`, `docs:`)

## Service & Test Patterns

These patterns are shared with `~/Repos/oxytec_evaluator` and apply to all Python services in this repo.

### Module Header

Every service module starts with:
```python
"""Short description — what the service does.

Issue: #XX

Key features:
- ...
"""

from __future__ import annotations
```

### Service Class Structure

```python
import structlog
from dataclasses import dataclass
from typing import Any

logger = structlog.get_logger(__name__)


@dataclass
class FooResult:
    """Result object — always a dataclass with to_dict()."""
    value: int
    method: str

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "method": self.method}


class FooService:
    """One-line summary.

    Longer description of what it does and why.

    Example usage:
        svc = FooService()
        result = svc.calculate(...)
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    def calculate(self, x: int) -> FooResult:
        """Calculate something.

        Args:
            x: Input value.

        Returns:
            FooResult with value and method used.
        """
        self.logger.info("foo_calculation_started", x=x)
        result = self._do_work(x)
        self.logger.info("foo_calculation_completed", value=result.value)
        return result

    def _do_work(self, x: int) -> FooResult:
        """Private helpers prefixed with _."""
        ...


# Singleton
_foo_service: FooService | None = None


def get_foo_service() -> FooService:
    global _foo_service
    if _foo_service is None:
        _foo_service = FooService()
    return _foo_service
```

### Enums for Categorical Values

```python
from enum import Enum

class MatchType(str, Enum):
    PREFIX = "prefix"
    EXACT = "exact"
    PATTERN = "pattern"
```

### Unit Test Structure

```python
"""Unit tests for FooService.

Tests: <what is covered>
"""

import pytest
from app.services.foo_service import FooService, FooResult


class TestFooService:
    """Test suite for FooService."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.service = FooService()

    def test_basic_case(self) -> None:
        """One behaviour per test, descriptive name."""
        result = self.service.calculate(5)
        assert result.value == 5

    # Group related tests with comments:
    # ============================================================================
    # EDGE CASES
    # ============================================================================

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError, match="x is required"):
            self.service.calculate(0)
```

**Rules:**
- `class TestXxx` with `setup_method` — never bare functions at module level
- One assertion per behaviour (multiple `assert` lines for one result are fine)
- Descriptive names: `test_<what>_<condition>_<expected>`
- Group with `# ===` comment blocks: OVERRIDE RULES, EDGE CASES, etc.
- No DB in unit tests — mock async sessions with `unittest.mock.AsyncMock`

### Async DB Services

For services that need a DB session, inject it as a parameter (not via singleton):

```python
from sqlalchemy.ext.asyncio import AsyncSession

class ZoneCalculatorService:
    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    async def calculate_zone(self, db: AsyncSession, ...) -> int:
        ...
```

Unit tests mock the session:
```python
from unittest.mock import AsyncMock, MagicMock

def setup_method(self) -> None:
    self.service = ZoneCalculatorService()
    self.db = AsyncMock(spec=AsyncSession)
```

---

**Last Updated:** 2026-03-19
**Version:** 2.0 (Python/FastAPI migration — Phase 0)
