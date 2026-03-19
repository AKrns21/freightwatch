# FreightWatch

Multi-tenant B2B SaaS platform for freight cost analysis. Parses invoices from carriers (CSV/Excel/PDF), calculates expected costs using tariff tables, and identifies overpayment opportunities.

## Stack

- **Backend:** Python 3.11 + FastAPI + SQLAlchemy 2.0 async + Alembic
- **Frontend:** React 19 + TypeScript + Vite + TailwindCSS
- **Database:** Supabase PostgreSQL (RLS-enforced multi-tenancy)
- **LLM:** Anthropic Claude (PDF Vision OCR, carrier detection)

## Project Structure

```
Repository/
├── backend/              # FastAPI Backend (Python)
│   ├── main.py           # Entry point
│   ├── pyproject.toml    # Dependencies
│   ├── app/              # Application code
│   └── tests/            # pytest test suite
├── backend_legacy/       # NestJS/TypeScript (archived reference implementation)
├── frontend/             # React + Vite Frontend
├── supabase/             # Supabase config + SQL migrations
├── docker-compose.yml    # PostgreSQL (local dev)
└── docs/                 # Architecture and migration docs
```

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose

### Setup

1. **Start PostgreSQL:**
   ```bash
   docker compose up -d
   ```

2. **Backend:**
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   cp .env.example .env    # fill in Supabase credentials
   alembic upgrade head
   uvicorn main:app --reload --port 4000
   ```

3. **Frontend:**
   ```bash
   cd frontend
   npm install
   cp .env.example .env    # set VITE_API_URL=http://localhost:4000
   npm run dev
   ```

### Services

- **API:** `http://localhost:4000`
- **API docs:** `http://localhost:4000/docs`
- **Frontend:** `http://localhost:5173`
- **PostgreSQL:** `localhost:5432`

## Development

### Backend

```bash
uvicorn main:app --reload --port 4000   # dev server
pytest                                   # run tests
pytest --cov=app --cov-report=html       # with coverage
ruff check . && ruff format .            # lint + format
mypy .                                   # type check
alembic upgrade head                     # apply migrations
```

### Frontend

```bash
npm run dev      # dev server
npm run build    # production build
npm run lint     # lint
```

## Documentation

- [Architecture](ARCHITECTURE.md) — full platform architecture and data model
- [CLAUDE.md](CLAUDE.md) — development guidance for Claude Code
- [Python Migration Plan](docs/REFACTORING_PYTHON_MIGRATION.md) — migration rationale and phases
