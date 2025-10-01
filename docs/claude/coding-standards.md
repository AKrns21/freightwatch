# Coding Standards

This document defines code style, naming conventions, and best practices for FreightWatch development.

## TypeScript Style

### Strict Mode

**`tsconfig.json` MUST have:**
```json
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "strictFunctionTypes": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true
  }
}
```

### Type Annotations

**Always annotate public method return types:**
```typescript
// ✅ GOOD
async calculateBenchmark(shipment: Shipment): Promise<ShipmentBenchmark> {
  // ...
}

// ❌ BAD
async calculateBenchmark(shipment: Shipment) {
  // TypeScript infers return type, but it's not obvious to readers
}
```

**Function parameters should have explicit types:**
```typescript
// ✅ GOOD
function round(value: number): number {
  return Math.round(value * 100) / 100;
}

// ❌ BAD
function round(value) {
  return Math.round(value * 100) / 100;
}
```

**Avoid `any`:**
```typescript
// ❌ BAD
function parseData(data: any): any {
  return JSON.parse(data);
}

// ✅ GOOD
function parseData<T>(data: string): T {
  return JSON.parse(data) as T;
}
```

### Interfaces vs Types

**Prefer interfaces for object shapes:**
```typescript
// ✅ GOOD
interface Shipment {
  id: string;
  tenant_id: string;
  carrier_id: string;
  date: Date;
  weight_kg: number;
}

// ❌ BAD (for objects)
type Shipment = {
  id: string;
  // ...
};
```

**Use types for unions, intersections, and utilities:**
```typescript
// ✅ GOOD
type Classification = 'unter' | 'im_markt' | 'drüber';
type DieselBasis = 'base' | 'base_plus_toll' | 'total';

type ShipmentWithBenchmark = Shipment & {
  benchmark: ShipmentBenchmark;
};
```

### Null Safety

**Use optional chaining and nullish coalescing:**
```typescript
// ✅ GOOD
const zone = shipment.zone_de ?? shipment.zone_at ?? null;
const carrierName = carrier?.name ?? 'Unknown';

// ❌ BAD
const zone = shipment.zone_de || shipment.zone_at || null; // 0 is falsy!
const carrierName = carrier ? carrier.name : 'Unknown';
```

**Check for null/undefined explicitly:**
```typescript
// ✅ GOOD
if (tariff === null) {
  throw new Error('Tariff not found');
}

// ❌ BAD
if (!tariff) {
  // This catches 0, '', false, null, undefined - probably not what you want
}
```

## Naming Conventions

### General Rules

- **camelCase**: Variables, functions, methods
- **PascalCase**: Classes, interfaces, types, enums
- **kebab-case**: File names, directories
- **snake_case**: Database columns, table names
- **SCREAMING_SNAKE_CASE**: Constants

### Examples

```typescript
// Variables & functions
const tenantId = 'abc-123';
const shipmentCount = 42;
function calculateZone(zipCode: string): number { }

// Classes & interfaces
class TariffEngine { }
interface ShipmentBenchmark { }
type ClassificationResult = { };

// Enums
enum ServiceCategory {
  Standard = 'standard',
  Premium = 'premium',
  Economy = 'economy'
}

// Constants
const MAX_BATCH_SIZE = 1000;
const DEFAULT_CURRENCY = 'EUR';

// Files
tariff-engine.ts
zone-calculator.service.ts
shipment.entity.ts
```

### Database Naming

```sql
-- Tables: snake_case, plural
CREATE TABLE shipments (...);
CREATE TABLE tariff_tables (...);

-- Columns: snake_case
tenant_id UUID
created_at TIMESTAMPTZ
actual_total_amount DECIMAL

-- Indexes: idx_<table>_<column(s)>
CREATE INDEX idx_shipment_tenant ON shipment(tenant_id);
CREATE INDEX idx_tariff_lookup ON tariff_table(tenant_id, carrier_id, zone);

-- Foreign keys: fk_<table>_<column>
CONSTRAINT fk_shipment_carrier FOREIGN KEY (carrier_id) REFERENCES carrier(id)
```

### Boolean Names

**Use `is`, `has`, `can`, `should` prefixes:**
```typescript
// ✅ GOOD
const isValid = true;
const hasZone = !!shipment.zone_de;
const canCalculate = tariff !== null;
const shouldRetry = attempt < 3;

// ❌ BAD
const valid = true;
const zone = !!shipment.zone_de;
```

### Method Names

**Use verb prefixes:**
```typescript
// ✅ GOOD
calculateZone()
findTariff()
parseCSV()
validateShipment()
convertCurrency()

// ❌ BAD
zone()
tariff()
csv()
```

## Project Structure

```
backend/
├── src/
│   ├── modules/              # Feature modules
│   │   ├── auth/
│   │   │   ├── auth.controller.ts
│   │   │   ├── auth.service.ts
│   │   │   ├── auth.module.ts
│   │   │   ├── guards/
│   │   │   │   └── jwt-auth.guard.ts
│   │   │   └── strategies/
│   │   │       └── jwt.strategy.ts
│   │   ├── tariff/
│   │   │   ├── tariff.controller.ts
│   │   │   ├── tariff.service.ts
│   │   │   ├── tariff.module.ts
│   │   │   ├── entities/
│   │   │   │   ├── tariff-table.entity.ts
│   │   │   │   ├── tariff-rule.entity.ts
│   │   │   │   └── diesel-floater.entity.ts
│   │   │   ├── engines/
│   │   │   │   ├── tariff-engine.ts
│   │   │   │   ├── zone-calculator.ts
│   │   │   │   └── weight-calculator.ts
│   │   │   └── dto/
│   │   │       ├── create-tariff.dto.ts
│   │   │       └── update-tariff.dto.ts
│   │   └── ...
│   ├── database/
│   │   ├── migrations/
│   │   │   ├── 001_create_tenant_table.ts
│   │   │   ├── 002_create_user_table.ts
│   │   │   └── ...
│   │   └── seeds/
│   │       ├── dev/
│   │       └── test/
│   ├── utils/
│   │   ├── round.ts            # CRITICAL: Use for all monetary calculations
│   │   ├── date-parser.ts
│   │   └── hash.ts
│   ├── types/
│   │   ├── shipment.types.ts
│   │   └── tariff.types.ts
│   ├── config/
│   │   └── database.config.ts
│   └── main.ts
├── test/
│   ├── fixtures/
│   │   └── mecu/
│   ├── integration/
│   └── unit/
├── tsconfig.json
├── package.json
└── .eslintrc.js
```

### Module Organization

Each module should follow this structure:
```
module-name/
├── module-name.controller.ts   # API endpoints
├── module-name.service.ts      # Business logic
├── module-name.module.ts       # NestJS module definition
├── entities/                   # Database entities
├── dto/                        # Data Transfer Objects
├── guards/                     # Authorization guards
├── pipes/                      # Validation pipes
└── tests/                      # Module-specific tests
```

## Import Organization

**Group and order imports:**
```typescript
// 1. Node.js built-ins
import * as fs from 'fs';
import * as path from 'path';

// 2. External dependencies
import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';

// 3. Internal modules (use @ alias)
import { round } from '@/utils/round';
import { Shipment } from '@/modules/shipment/entities/shipment.entity';
import { TariffService } from '@/modules/tariff/tariff.service';

// 4. Relative imports (avoid if possible, prefer @ alias)
import { LocalHelper } from './helpers/local-helper';
```

**Configure @ alias in `tsconfig.json`:**
```json
{
  "compilerOptions": {
    "baseUrl": "./src",
    "paths": {
      "@/*": ["*"]
    }
  }
}
```

## Error Handling

### Exception Classes

**Use NestJS built-in exceptions:**
```typescript
import {
  BadRequestException,
  NotFoundException,
  UnauthorizedException,
  ForbiddenException,
  InternalServerErrorException
} from '@nestjs/common';

// ✅ GOOD
if (!shipment) {
  throw new NotFoundException(`Shipment ${id} not found`);
}

if (!hasPermission) {
  throw new ForbiddenException('Insufficient permissions');
}

if (invalidData) {
  throw new BadRequestException({
    message: 'Invalid shipment data',
    errors: validationErrors
  });
}
```

### Custom Business Exceptions

```typescript
// src/exceptions/business-rule.exception.ts
export class BusinessRuleException extends BadRequestException {
  constructor(
    public readonly code: string,
    message: string,
    public readonly context?: Record<string, any>
  ) {
    super({
      statusCode: 400,
      error: 'Business Rule Violation',
      code,
      message,
      context
    });
  }
}

// Usage
throw new BusinessRuleException(
  'ZONE_NOT_FOUND',
  `Could not determine zone for ${zipCode}`,
  {
    shipment_id: shipmentId,
    carrier_id: carrierId,
    dest_zip: zipCode
  }
);
```

### Try-Catch Best Practices

```typescript
// ✅ GOOD: Specific error handling
try {
  const benchmark = await this.calculateBenchmark(shipment);
  return benchmark;
} catch (error) {
  if (error instanceof BusinessRuleException) {
    this.logger.warn({
      event: 'benchmark_calculation_failed',
      shipment_id: shipment.id,
      error: error.message,
      context: error.context
    });
    throw error;
  }
  
  // Unexpected error
  this.logger.error({
    event: 'benchmark_calculation_error',
    shipment_id: shipment.id,
    error: error.message,
    stack: error.stack
  });
  
  throw new InternalServerErrorException(
    'Failed to calculate benchmark'
  );
}

// ❌ BAD: Generic catch with no context
try {
  const benchmark = await this.calculateBenchmark(shipment);
  return benchmark;
} catch (error) {
  throw error;
}
```

## Logging

### Structured Logging

**Use Winston with JSON format:**
```typescript
import { Logger } from '@nestjs/common';

export class TariffEngine {
  private readonly logger = new Logger(TariffEngine.name);
  
  async calculate(shipment: Shipment): Promise<Benchmark> {
    this.logger.log({
      event: 'benchmark_calculation_start',
      shipment_id: shipment.id,
      tenant_id: shipment.tenant_id,
      carrier_id: shipment.carrier_id
    });
    
    // ... calculation logic
    
    this.logger.log({
      event: 'benchmark_calculation_complete',
      shipment_id: shipment.id,
      expected_total: benchmark.expected_total_amount,
      delta_pct: benchmark.delta_pct,
      classification: benchmark.classification,
      duration_ms: Date.now() - startTime
    });
    
    return benchmark;
  }
}
```

### Log Levels

**Use appropriate log levels:**
```typescript
// DEBUG: Detailed diagnostic info (only in development)
this.logger.debug({
  event: 'zone_lookup_attempt',
  zip: zipCode,
  prefix_len: len
});

// INFO: Normal operations
this.logger.log({
  event: 'upload_complete',
  upload_id: uploadId,
  shipment_count: count
});

// WARN: Recoverable issues
this.logger.warn({
  event: 'missing_ldm_rule',
  carrier_id: carrierId,
  message: 'Using actual weight instead'
});

// ERROR: Failures requiring attention
this.logger.error({
  event: 'tariff_lookup_failed',
  shipment_id: shipmentId,
  error: error.message,
  stack: error.stack
});
```

## Comments

### When to Comment

**DO comment:**
- Complex algorithms or business logic
- Non-obvious workarounds
- External API integrations
- Public API/interface documentation (JSDoc)

**DON'T comment:**
- Obvious code (`// Set x to 5`)
- What the code does (code should be self-explanatory)
- Version history (use Git)

### JSDoc for Public APIs

```typescript
/**
 * Calculates the expected cost for a shipment based on tariff rules.
 * 
 * @param shipment - The shipment to calculate costs for
 * @returns Benchmark result with expected costs and delta
 * @throws {BusinessRuleException} If zone cannot be determined
 * @throws {NotFoundException} If no applicable tariff found
 * 
 * @example
 * ```typescript
 * const benchmark = await engine.calculateBenchmark({
 *   id: 'uuid',
 *   tenant_id: 'tenant-uuid',
 *   carrier_id: 'dhl-uuid',
 *   date: new Date('2023-12-01'),
 *   weight_kg: 500,
 *   // ...
 * });
 * ```
 */
async calculateBenchmark(shipment: Shipment): Promise<ShipmentBenchmark> {
  // ...
}
```

### Inline Comments

```typescript
// ✅ GOOD: Explains WHY
// Use MAX instead of actual weight because volumetric weight may be higher
const chargeableWeight = Math.max(weightKg, ldm * ldmToKg);

// ❌ BAD: Explains WHAT (obvious from code)
// Get the chargeable weight
const chargeableWeight = Math.max(weightKg, ldm * ldmToKg);
```

## Code Formatting

### Prettier Configuration

**`.prettierrc`:**
```json
{
  "semi": true,
  "trailingComma": "es5",
  "singleQuote": true,
  "printWidth": 100,
  "tabWidth": 2,
  "arrowParens": "always"
}
```

### ESLint Configuration

**`.eslintrc.js`:**
```javascript
module.exports = {
  parser: '@typescript-eslint/parser',
  extends: [
    'plugin:@typescript-eslint/recommended',
    'plugin:prettier/recommended'
  ],
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module'
  },
  rules: {
    '@typescript-eslint/explicit-function-return-type': 'error',
    '@typescript-eslint/no-explicit-any': 'error',
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    'no-console': ['warn', { allow: ['warn', 'error'] }]
  }
};
```

## Git Conventions

### Commit Messages

**Use Conventional Commits:**
```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `test`: Adding or updating tests
- `docs`: Documentation only
- `chore`: Maintenance tasks (deps, config)
- `perf`: Performance improvement

**Examples:**
```
feat(tariff): add diesel floater interval support

Replaced monthly diesel floater with valid_from/valid_until pattern
to support mid-month diesel changes.

Closes #42

---

fix(parsing): handle empty CSV rows

Skip empty rows during CSV parsing to prevent validation errors.

---

refactor(engine): extract zone calculator into separate service

Moved zone calculation logic from TariffEngine into ZoneCalculator
for better testability and reusability.
```

### Branch Naming

```
feature/tariff-interval-support
fix/csv-parsing-empty-rows
refactor/extract-zone-calculator
docs/update-readme
chore/upgrade-nestjs
```

## Performance Best Practices

### Avoid N+1 Queries

```typescript
// ❌ BAD: N+1 queries
for (const shipment of shipments) {
  const carrier = await carrierRepo.findOne({ id: shipment.carrier_id });
  // ...
}

// ✅ GOOD: Single query with JOIN
const shipments = await shipmentRepo.find({
  where: { upload_id: uploadId },
  relations: ['carrier']
});
```

### Batch Operations

```typescript
// ❌ BAD: Sequential inserts
for (const shipment of shipments) {
  await shipmentRepo.save(shipment);
}

// ✅ GOOD: Batch insert
await shipmentRepo.insert(shipments);
```

### Parallel Processing

```typescript
// ❌ BAD: Sequential
for (const shipment of shipments) {
  await calculateBenchmark(shipment);
}

// ✅ GOOD: Parallel (with concurrency limit)
const pLimit = (await import('p-limit')).default;
const limit = pLimit(5); // Max 5 concurrent

const promises = shipments.map((shipment) =>
  limit(() => calculateBenchmark(shipment))
);

await Promise.all(promises);
```

### Caching

```typescript
// Example: Cache zone mappings (rarely change)
@Injectable()
export class ZoneCalculator {
  private zoneCache = new Map<string, number>();
  
  async calculateZone(
    tenantId: string,
    carrierId: string,
    country: string,
    zipCode: string
  ): Promise<number | null> {
    const cacheKey = `${tenantId}:${carrierId}:${country}:${zipCode}`;
    
    if (this.zoneCache.has(cacheKey)) {
      return this.zoneCache.get(cacheKey)!;
    }
    
    const zone = await this.lookupZone(tenantId, carrierId, country, zipCode);
    
    if (zone) {
      this.zoneCache.set(cacheKey, zone);
    }
    
    return zone;
  }
}
```

## Security Best Practices

### Never Log Sensitive Data

```typescript
// ❌ BAD
this.logger.log({
  event: 'user_login',
  email: user.email,
  password: user.password  // NEVER!
});

// ✅ GOOD
this.logger.log({
  event: 'user_login',
  user_id: user.id,
  email: user.email
});
```

### Validate All Inputs

```typescript
import { IsString, IsNumber, Min, Max } from 'class-validator';

export class CreateShipmentDto {
  @IsString()
  carrier_id: string;
  
  @IsNumber()
  @Min(0)
  @Max(999999)
  weight_kg: number;
  
  @IsString()
  @Matches(/^\d{5}$/)
  dest_zip: string;
}
```

### SQL Injection Prevention

**Always use parameterized queries:**
```typescript
// ✅ GOOD: Parameterized query
await db.query(
  'SELECT * FROM shipment WHERE tenant_id = $1 AND carrier_id = $2',
  [tenantId, carrierId]
);

// ❌ BAD: String concatenation (SQL injection risk)
await db.query(
  `SELECT * FROM shipment WHERE tenant_id = '${tenantId}'`
);
```

## References

- TypeScript Handbook: https://www.typescriptlang.org/docs/
- NestJS Docs: https://docs.nestjs.com/
- Conventional Commits: https://www.conventionalcommits.org/
- ESLint: https://eslint.org/
- Prettier: https://prettier.io/