# FreightWatch

Freight cost analysis system for analyzing and comparing shipping tariffs.

## Project Structure

```
Repository/
├── backend/                 # NestJS API Backend
│   ├── src/
│   │   ├── modules/        # Feature modules
│   │   │   ├── auth/       # Authentication & authorization
│   │   │   ├── upload/     # File upload handling
│   │   │   ├── parsing/    # Tariff file parsing
│   │   │   ├── tariff/     # Tariff management
│   │   │   └── report/     # Report generation
│   │   ├── database/       # Database related files
│   │   │   ├── migrations/ # TypeORM migrations
│   │   │   └── seeds/      # Database seed files
│   │   └── utils/          # Utility functions
│   ├── test/              # Test files
│   └── package.json
├── frontend/              # Frontend (to be implemented)
├── docker-compose.yml     # Docker services
└── README.md
```

## Getting Started

### Prerequisites

- Node.js (v18+)
- Docker & Docker Compose
- PostgreSQL (via Docker)
- Redis (via Docker)

### Installation

1. **Start services:**
   ```bash
   docker compose up -d
   ```

2. **Install dependencies:**
   ```bash
   cd backend
   npm install
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   ```

4. **Start development server:**
   ```bash
   npm run start:dev
   ```

The API will be available at `http://localhost:3000/api`

### Services

- **PostgreSQL:** `localhost:5432`
- **Redis:** `localhost:6379`
- **API:** `http://localhost:3000/api`

## Development

### Available Scripts

```bash
npm run start:dev    # Start development server
npm run build        # Build for production
npm run test         # Run tests
npm run lint         # Lint code
```

### Module Structure

Each module follows NestJS conventions:
- `*.module.ts` - Module definition
- `*.controller.ts` - HTTP routes
- `*.service.ts` - Business logic
- `*.entity.ts` - Database entities
- `*.dto.ts` - Data transfer objects