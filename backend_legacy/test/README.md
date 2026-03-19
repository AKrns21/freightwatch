# FreightWatch Integration Tests

This directory contains integration tests for the FreightWatch backend system.

## MECU Dataset Validation

The `integration/mecu-validation.spec.ts` file contains comprehensive end-to-end tests that validate the complete freight processing pipeline.

### Test Structure

#### Setup
- Creates test tenant 'MECU Test'
- Seeds carrier 'COSI' with alias mapping
- Inserts sample tariff for zone 3, 400-500kg: 294.30 EUR
- Inserts diesel floater: 18.5% from 2023-01-01
- Inserts zone mapping: PLZ 60xxx → zone 3

#### Test Cases

1. **CSV Parsing and Shipment Creation**
   - Uploads test CSV with 10 sample shipments
   - Waits for background job queue processing
   - Validates 10 shipments are created in database
   - Verifies carrier mapping and data integrity

2. **Benchmark Calculation Validation**
   - Validates all shipments have benchmark records
   - Verifies zone 3 / 450kg has expected_base_amount ≈ 294.30 EUR
   - Checks diesel surcharge calculation (18.5%)
   - Validates total expected cost calculation

3. **Overpay/Underpay Detection**
   - Tests 'drüber' classification for overpaid shipments
   - Tests 'unter' classification for underpaid shipments  
   - Validates delta amount and percentage calculations
   - Verifies benchmark record persistence

### Test Data

The test uses sample CSV data from `fixtures/mecu/sample.csv` containing:
- 10 shipments with various weights (400-500kg)
- Frankfurt area postal codes (60xxx)
- Munich destination codes (80xxx)
- Mix of prices for classification testing

### Prerequisites

1. **Database Setup**
   - PostgreSQL test database: `freightwatch_test`
   - Run migrations on test database
   - Ensure database is clean before running tests

2. **Redis Setup**
   - Redis server running on localhost:6379
   - Used for Bull job queue processing

3. **Environment Configuration**
   - Copy `.env.test` and configure test database credentials
   - Ensure test environment variables are set

### Running the Tests

```bash
# Run all integration tests
npm run test:e2e

# Run only MECU validation tests
npm run test:integration

# Run with verbose output and watch mode
npm run test:e2e -- --verbose --detectOpenHandles

# Run single test file
npm run test:e2e -- test/integration/mecu-validation.spec.ts
```

### Test Cleanup

Tests automatically clean up all test data after completion:
- Removes all benchmark records
- Removes all test shipments
- Removes test upload records
- Removes test tariff data
- Removes test tenant and carrier

### Debugging

Enable detailed logging by setting these environment variables in `.env.test`:
```
DB_LOGGING=true
NODE_ENV=test
```

View job queue status during tests:
- Tests include queue monitoring and timeout handling
- Failed jobs will show detailed error information
- Use Redis CLI to inspect queue state: `redis-cli monitor`

### Expected Test Results

All tests should pass with the following key validations:
- ✅ 10 shipments created from CSV upload
- ✅ All shipments have benchmark calculations
- ✅ Zone 3 tariff correctly applied (294.30 EUR base)
- ✅ Diesel surcharge correctly calculated (18.5%)
- ✅ Classification logic works ('drüber', 'im_markt', 'unter')
- ✅ Delta calculations are accurate

### Common Issues

1. **Queue Processing Timeout**
   - Increase timeout in test configuration
   - Check Redis connection
   - Verify background job processor is running

2. **Database Connection Issues**
   - Ensure test database exists and is accessible
   - Check test database credentials in `.env.test`
   - Verify migrations have been run

3. **Missing Test Data**
   - Tests create their own isolated test data
   - Each test run uses unique tenant/carrier IDs
   - Cleanup should handle all test data removal