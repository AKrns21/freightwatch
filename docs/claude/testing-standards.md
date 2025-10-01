# Testing Standards

This document defines testing strategies, patterns, and quality metrics for FreightWatch.

## Testing Philosophy

1. **Test business logic, not implementation details**
2. **Test cross-tenant isolation rigorously**
3. **Use real MECU data for validation tests**
4. **Aim for >80% code coverage, but prioritize critical paths**
5. **Fast unit tests, comprehensive integration tests**

## Test Pyramid

```
        /\
       /  \
      / E2E \        < 10% - Full flow tests
     /------\
    /        \
   / Integration \   ~ 30% - API + DB tests
  /--------------\
 /                \
/   Unit Tests     \ ~ 60% - Pure logic tests
--------------------
```

## Unit Tests

Unit tests verify individual functions/methods in isolation with mocked dependencies.

### What to Unit Test

- Pure functions (round, date parsing, currency conversion)
- Business logic (zone calculation, weight calculation)
- Validation logic
- Data transformers

### Unit Test Template

```typescript
import { round } from '@/utils/round';

describe('round()', () => {
  it('rounds to 2 decimal places', () => {
    expect(round(10.126)).toBe(10.13);
    expect(round(10.124)).toBe(10.12);
  });
  
  it('handles negative numbers', () => {
    expect(round(-10.126)).toBe(-10.13);
  });
  
  it('preserves integers', () => {
    expect(round(10)).toBe(10.0);
    expect(round(0)).toBe(0.0);
  });
  
  it('handles very small numbers', () => {
    expect(round(0.001)).toBe(0.0);
    expect(round(0.009)).toBe(0.01);
  });
  
  it('handles edge cases', () => {
    expect(round(NaN)).toBeNaN();
    expect(round(Infinity)).toBe(Infinity);
  });
});
```

### Mocking Dependencies

**Use Jest mocks for external dependencies:**

```typescript
import { ZoneCalculator } from '@/modules/tariff/engines/zone-calculator';
import { TariffService } from '@/modules/tariff/tariff.service';

describe('TariffEngine', () => {
  let engine: TariffEngine;
  let mockZoneCalc: jest.Mocked<ZoneCalculator>;
  let mockTariffService: jest.Mocked<TariffService>;
  
  beforeEach(() => {
    // Create mock instances
    mockZoneCalc = {
      calculateZone: jest.fn()
    } as any;
    
    mockTariffService = {
      findTariff: jest.fn()
    } as any;
    
    // Inject mocks into engine
    engine = new TariffEngine(
      mockZoneCalc,
      mockTariffService,
      // ... other deps
    );
  });
  
  it('calculates benchmark with mocked zone', async () => {
    // Setup mock responses
    mockZoneCalc.calculateZone.mockResolvedValue(3);
    mockTariffService.findTariff.mockResolvedValue({
      id: 'tariff-uuid',
      base_amount: 100.00,
      currency: 'EUR'
    });
    
    const shipment = createTestShipment({
      weight_kg: 500,
      dest_zip: '80331'
    });
    
    const benchmark = await engine.calculateBenchmark(shipment);
    
    expect(benchmark.expected_base_amount).toBe(100.00);
    expect(mockZoneCalc.calculateZone).toHaveBeenCalledWith(
      shipment.tenant_id,
      shipment.carrier_id,
      'DE',
      '80331',
      shipment.date
    );
  });
});
```

### Test Helpers

**Create reusable test data builders:**

```typescript
// test/helpers/test-data.ts
export function createTestShipment(overrides: Partial<Shipment> = {}): Shipment {
  return {
    id: 'test-shipment-uuid',
    tenant_id: 'test-tenant-uuid',
    carrier_id: 'test-carrier-uuid',
    upload_id: 'test-upload-uuid',
    date: new Date('2023-12-01'),
    origin_zip: '80331',
    origin_country: 'DE',
    dest_zip: '10115',
    dest_country: 'DE',
    weight_kg: 500,
    ldm: null,
    currency: 'EUR',
    actual_total_amount: 125.50,
    service_level: 'STANDARD',
    source_data: {},
    created_at: new Date(),
    updated_at: new Date(),
    deleted_at: null,
    ...overrides
  };
}

export function createTestTariff(overrides: Partial<Tariff> = {}): Tariff {
  return {
    id: 'test-tariff-uuid',
    tenant_id: 'test-tenant-uuid',
    carrier_id: 'test-carrier-uuid',
    lane_type: 'domestic_de',
    zone: 1,
    weight_min: 0,
    weight_max: 1000,
    base_amount: 100.00,
    currency: 'EUR',
    valid_from: new Date('2023-01-01'),
    valid_until: null,
    ...overrides
  };
}
```

## Integration Tests

Integration tests verify interactions between components and database operations.

### Database Setup

**Use transaction rollback for cleanup:**

```typescript
import { Test, TestingModule } from '@nestjs/testing';
import { TypeOrmModule } from '@nestjs/typeorm';
import { DataSource, QueryRunner } from 'typeorm';

describe('ShipmentService (integration)', () => {
  let app: TestingModule;
  let dataSource: DataSource;
  let queryRunner: QueryRunner;
  let shipmentService: ShipmentService;
  
  beforeAll(async () => {
    app = await Test.createTestingModule({
      imports: [
        TypeOrmModule.forRoot({
          type: 'postgres',
          host: process.env.DB_HOST || 'localhost',
          port: 5432,
          database: 'freightwatch_test',
          entities: [__dirname + '/../**/*.entity.ts'],
          synchronize: false // Use migrations
        }),
        ShipmentModule
      ]
    }).compile();
    
    dataSource = app.get(DataSource);
    shipmentService = app.get(ShipmentService);
  });
  
  beforeEach(async () => {
    // Start transaction
    queryRunner = dataSource.createQueryRunner();
    await queryRunner.connect();
    await queryRunner.startTransaction();
  });
  
  afterEach(async () => {
    // Rollback transaction (auto-cleanup)
    await queryRunner.rollbackTransaction();
    await queryRunner.release();
  });
  
  afterAll(async () => {
    await app.close();
  });
  
  it('creates shipment with tenant context', async () => {
    const tenant = await createTestTenant(queryRunner);
    
    // Set tenant context
    await dataSource.query(
      'SET LOCAL app.current_tenant = $1',
      [tenant.id]
    );
    
    const shipment = await shipmentService.create({
      tenant_id: tenant.id,
      carrier_id: 'test-carrier',
      date: new Date('2023-12-01'),
      weight_kg: 500,
      // ...
    });
    
    expect(shipment.id).toBeDefined();
    expect(shipment.tenant_id).toBe(tenant.id);
  });
});
```

### RLS Isolation Tests

**CRITICAL: Always test cross-tenant isolation:**

```typescript
describe('RLS Isolation', () => {
  let tenant1: Tenant;
  let tenant2: Tenant;
  
  beforeEach(async () => {
    tenant1 = await createTestTenant(queryRunner, { name: 'Tenant A' });
    tenant2 = await createTestTenant(queryRunner, { name: 'Tenant B' });
  });
  
  it('prevents cross-tenant data access', async () => {
    // Create shipment for tenant1
    await dataSource.query(
      'SET LOCAL app.current_tenant = $1',
      [tenant1.id]
    );
    
    const shipment = await shipmentRepo.save({
      tenant_id: tenant1.id,
      carrier_id: 'test-carrier',
      date: new Date(),
      weight_kg: 500,
      actual_total_amount: 100.00,
      currency: 'EUR'
    });
    
    // Switch to tenant2
    await dataSource.query(
      'SET LOCAL app.current_tenant = $1',
      [tenant2.id]
    );
    
    // Should NOT find tenant1's shipment
    const found = await shipmentRepo.findOne({ 
      where: { id: shipment.id } 
    });
    
    expect(found).toBeNull();
  });
  
  it('allows same-tenant data access', async () => {
    await dataSource.query(
      'SET LOCAL app.current_tenant = $1',
      [tenant1.id]
    );
    
    const shipment = await shipmentRepo.save({
      tenant_id: tenant1.id,
      // ...
    });
    
    // Same tenant can access
    const found = await shipmentRepo.findOne({ 
      where: { id: shipment.id } 
    });
    
    expect(found).toBeDefined();
    expect(found!.id).toBe(shipment.id);
  });
  
  it('allows global data access from all tenants', async () => {
    // Create global carrier (NULL tenant_id)
    const carrier = await carrierRepo.save({
      tenant_id: null,
      name: 'Global Carrier',
      code: 'GLOBAL'
    });
    
    // Tenant1 can see global carrier
    await dataSource.query(
      'SET LOCAL app.current_tenant = $1',
      [tenant1.id]
    );
    
    const found1 = await carrierRepo.findOne({ 
      where: { id: carrier.id } 
    });
    expect(found1).toBeDefined();
    
    // Tenant2 can also see global carrier
    await dataSource.query(
      'SET LOCAL app.current_tenant = $1',
      [tenant2.id]
    );
    
    const found2 = await carrierRepo.findOne({ 
      where: { id: carrier.id } 
    });
    expect(found2).toBeDefined();
  });
});
```

### API Tests

**Test HTTP endpoints with authentication:**

```typescript
import * as request from 'supertest';
import { INestApplication } from '@nestjs/common';

describe('ShipmentController (e2e)', () => {
  let app: INestApplication;
  let authToken: string;
  let tenantId: string;
  
  beforeAll(async () => {
    const moduleFixture = await Test.createTestingModule({
      imports: [AppModule]
    }).compile();
    
    app = moduleFixture.createNestApplication();
    await app.init();
    
    // Login to get auth token
    const loginRes = await request(app.getHttpServer())
      .post('/auth/login')
      .send({ email: 'test@example.com', password: 'password' })
      .expect(200);
    
    authToken = loginRes.body.access_token;
    tenantId = loginRes.body.tenant_id;
  });
  
  afterAll(async () => {
    await app.close();
  });
  
  it('GET /shipments returns list with auth', () => {
    return request(app.getHttpServer())
      .get('/shipments')
      .set('Authorization', `Bearer ${authToken}`)
      .expect(200)
      .expect((res) => {
        expect(Array.isArray(res.body.data)).toBe(true);
      });
  });
  
  it('GET /shipments returns 401 without auth', () => {
    return request(app.getHttpServer())
      .get('/shipments')
      .expect(401);
  });
  
  it('POST /shipments creates shipment', () => {
    return request(app.getHttpServer())
      .post('/shipments')
      .set('Authorization', `Bearer ${authToken}`)
      .send({
        carrier_id: 'test-carrier-uuid',
        date: '2023-12-01',
        origin_zip: '80331',
        dest_zip: '10115',
        weight_kg: 500,
        actual_total_amount: 125.50,
        currency: 'EUR'
      })
      .expect(201)
      .expect((res) => {
        expect(res.body.data.id).toBeDefined();
        expect(res.body.data.tenant_id).toBe(tenantId);
      });
  });
});
```

## Validation Tests with Real Data

**Use MECU fixtures for validation:**

### MECU Test Suite

```typescript
import * as fs from 'fs';
import * as path from 'path';
import { parseCSV } from '@/modules/parsing/parsers/csv-parser';

describe('MECU Validation Suite', () => {
  const fixturesPath = path.join(__dirname, '../fixtures/mecu');
  
  it('parses MECU sample.csv with >90% success', async () => {
    const csvPath = path.join(fixturesPath, 'sample.csv');
    const content = fs.readFileSync(csvPath, 'utf-8');
    
    const result = await parseCSV(content);
    
    const totalRows = result.shipments.length + result.errors.length;
    const successRate = result.shipments.length / totalRows;
    
    expect(successRate).toBeGreaterThan(0.9);
    expect(result.errors.length).toBeLessThan(totalRows * 0.1);
  });
  
  it('calculates benchmarks for MECU data with >85% tariff match', async () => {
    const csvPath = path.join(fixturesPath, 'sample.csv');
    const shipments = await parseAndImportCSV(csvPath, tenantId);
    
    const benchmarks = await Promise.all(
      shipments.map(s => tariffEngine.calculateBenchmark(s))
    );
    
    const matched = benchmarks.filter(b => b.tariff_table_id !== null);
    const matchRate = matched.length / benchmarks.length;
    
    expect(matchRate).toBeGreaterThan(0.85);
  });
  
  it('identifies >50% of MECU overpayments', async () => {
    // Based on manual analysis, MECU has ~60% overpay rate
    const csvPath = path.join(fixturesPath, 'sample.csv');
    const shipments = await parseAndImportCSV(csvPath, tenantId);
    
    const benchmarks = await Promise.all(
      shipments.map(s => tariffEngine.calculateBenchmark(s))
    );
    
    const overpays = benchmarks.filter(b => b.classification === 'drüber');
    const overpayRate = overpays.length / benchmarks.length;
    
    expect(overpayRate).toBeGreaterThan(0.5);
  });
  
  it('matches known MECU expected costs within ±5%', async () => {
    // Test cases with manually verified expected costs
    const testCases = [
      {
        shipment: {
          date: new Date('2022-03-15'),
          carrier: 'MECU',
          origin_zip: '78628',
          dest_zip: '80331',
          weight_kg: 500,
          actual_total: 145.80,
          currency: 'EUR'
        },
        expected_base: 98.50,
        expected_diesel: 17.73, // 18% of base
        expected_toll: 22.00,
        expected_total: 138.23
      },
      // Add more known cases from MECU data
    ];
    
    for (const testCase of testCases) {
      const shipment = await createShipment(testCase.shipment);
      const benchmark = await tariffEngine.calculateBenchmark(shipment);
      
      const baseError = Math.abs(
        benchmark.expected_base_amount - testCase.expected_base
      ) / testCase.expected_base;
      
      const totalError = Math.abs(
        benchmark.expected_total_amount - testCase.expected_total
      ) / testCase.expected_total;
      
      expect(baseError).toBeLessThan(0.05); // Within 5%
      expect(totalError).toBeLessThan(0.05);
    }
  });
});
```

### Fixture Management

```
test/fixtures/
├── mecu/
│   ├── sample.csv              # 100 representative shipments
│   ├── heavy-goods.csv         # Edge case: heavy shipments
│   ├── international.csv       # Edge case: DE-CH, DE-AT
│   └── multi-currency.csv      # Edge case: CHF, USD
├── carriers/
│   ├── dhl-tariff.csv
│   ├── fedex-tariff.csv
│   └── gebrweiss-tariff.csv
└── expected/
    └── mecu-benchmarks.json    # Manually verified expected results
```

## Performance Tests

### Benchmark Processing Speed

```typescript
describe('Performance Tests', () => {
  it('processes 10k shipments in <30 seconds', async () => {
    const shipments = Array.from({ length: 10000 }, (_, i) =>
      createTestShipment({
        id: `shipment-${i}`,
        weight_kg: 100 + (i % 500)
      })
    );
    
    const startTime = Date.now();
    
    await benchmarkService.calculateBulk(shipments);
    
    const duration = Date.now() - startTime;
    
    expect(duration).toBeLessThan(30000); // 30 seconds
  }, 35000); // Jest timeout extended to 35s
});
```

### Database Query Performance

```typescript
describe('Query Performance', () => {
  it('tariff lookup completes in <100ms', async () => {
    const startTime = Date.now();
    
    const tariff = await tariffService.findTariff(
      tenantId,
      carrierId,
      'domestic_de',
      3, // zone
      500, // weight
      new Date()
    );
    
    const duration = Date.now() - startTime;
    
    expect(duration).toBeLessThan(100);
    expect(tariff).toBeDefined();
  });
  
  it('zone calculation completes in <50ms', async () => {
    const startTime = Date.now();
    
    const zone = await zoneCalculator.calculateZone(
      tenantId,
      carrierId,
      'DE',
      '80331',
      new Date()
    );
    
    const duration = Date.now() - startTime;
    
    expect(duration).toBeLessThan(50);
    expect(zone).toBeDefined();
  });
});
```

## Test Coverage

### Coverage Configuration

**`jest.config.js`:**
```javascript
module.exports = {
  moduleFileExtensions: ['js', 'json', 'ts'],
  rootDir: 'src',
  testRegex: '.*\\.spec\\.ts$',
  transform: {
    '^.+\\.(t|j)s$': 'ts-jest'
  },
  collectCoverageFrom: [
    '**/*.ts',
    '!**/*.entity.ts',      // Exclude entities
    '!**/*.dto.ts',         // Exclude DTOs
    '!**/*.module.ts',      // Exclude modules
    '!**/migrations/**',    // Exclude migrations
    '!**/main.ts'           // Exclude entry point
  ],
  coverageDirectory: '../coverage',
  coverageThresholds: {
    global: {
      branches: 80,
      functions: 80,
      lines: 80,
      statements: 80
    },
    // Critical paths require higher coverage
    './modules/tariff/engines/': {
      branches: 90,
      functions: 90,
      lines: 90,
      statements: 90
    }
  },
  testEnvironment: 'node'
};
```

### Coverage Reports

**Generate HTML report:**
```bash
npm run test:cov

# Open coverage report
open coverage/lcov-report/index.html
```

**Focus on critical paths:**
- Tariff engine: 90%+
- Zone calculator: 90%+
- Weight calculator: 90%+
- Diesel service: 85%+
- RLS policies: 100% (test all isolation scenarios)

## Quality Metrics

### Required Metrics

**Before merging to main:**
- ✅ All tests pass
- ✅ Code coverage ≥80% (90% for critical modules)
- ✅ RLS isolation tests pass for all tenant-scoped tables
- ✅ MECU validation tests pass (>90% parsing, >85% tariff match)
- ✅ No ESLint errors
- ✅ No TypeScript compilation errors

### CI/CD Pipeline

**GitHub Actions example:**
```yaml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:14
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: freightwatch_test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '18'
      
      - name: Install dependencies
        run: npm ci
      
      - name: Run migrations
        run: npm run migration:run
        env:
          DB_HOST: localhost
          DB_PORT: 5432
          DB_NAME: freightwatch_test
      
      - name: Run unit tests
        run: npm run test
      
      - name: Run integration tests
        run: npm run test:e2e
      
      - name: Run MECU validation
        run: npm run test:mecu
      
      - name: Generate coverage
        run: npm run test:cov
      
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
```

## Test Organization

### File Naming

```
src/modules/tariff/
├── tariff.service.ts
├── tariff.service.spec.ts        # Unit tests
├── tariff.controller.ts
├── tariff.controller.spec.ts     # Unit tests
└── tariff.e2e-spec.ts            # Integration/E2E tests
```

### Test Suite Structure

```typescript
describe('TariffEngine', () => {
  // Setup
  describe('setup', () => {
    it('initializes with dependencies', () => { });
  });
  
  // Happy path
  describe('calculateBenchmark', () => {
    it('calculates benchmark for standard shipment', () => { });
    it('calculates benchmark with LDM conversion', () => { });
    it('handles currency conversion', () => { });
  });
  
  // Edge cases
  describe('edge cases', () => {
    it('handles missing tariff', () => { });
    it('handles missing zone', () => { });
    it('handles zero weight', () => { });
  });
  
  // Error handling
  describe('error handling', () => {
    it('throws BusinessRuleException on missing zone', () => { });
    it('throws NotFoundException on missing tariff', () => { });
  });
});
```

## Best Practices

### DO

- ✅ Test behavior, not implementation
- ✅ Use descriptive test names (`it('calculates zone for Munich postal code')`)
- ✅ Arrange-Act-Assert pattern
- ✅ One assertion concept per test
- ✅ Mock external dependencies
- ✅ Test cross-tenant isolation
- ✅ Use real MECU data for validation
- ✅ Clean up test data (transaction rollback)

### DON'T

- ❌ Test framework internals (TypeORM, NestJS)
- ❌ Test getters/setters
- ❌ Duplicate tests
- ❌ Share state between tests
- ❌ Use real production data in tests
- ❌ Skip cleanup (causes flaky tests)
- ❌ Hardcode test data (use factories)

## References

- Jest Documentation: https://jestjs.io/
- NestJS Testing: https://docs.nestjs.com/fundamentals/testing
- TypeORM Testing: https://typeorm.io/testing
- Supertest: https://github.com/ladjs/supertest