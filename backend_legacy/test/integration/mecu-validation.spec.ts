import { Test, TestingModule } from '@nestjs/testing';
import { BullModule, getQueueToken } from '@nestjs/bull';
import { ConfigModule } from '@nestjs/config';
import { Repository } from 'typeorm';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Queue } from 'bull';
import * as path from 'path';
import * as fs from 'fs/promises';

// Import all required entities and services
import { DatabaseModule } from '../../src/database/database.module';
import { UploadModule } from '../../src/modules/upload/upload.module';
import { ParsingModule } from '../../src/modules/parsing/parsing.module';
import { TariffModule } from '../../src/modules/tariff/tariff.module';

// Import entities
import { Upload } from '../../src/modules/upload/entities/upload.entity';
import { CarrierAlias } from '../../src/modules/upload/entities/carrier-alias.entity';
import { Shipment } from '../../src/modules/parsing/entities/shipment.entity';
import { ShipmentBenchmark } from '../../src/modules/tariff/entities/shipment-benchmark.entity';
import { TariffZoneMap } from '../../src/modules/tariff/entities/tariff-zone-map.entity';
import { TariffTable } from '../../src/modules/tariff/entities/tariff-table.entity';
import { TariffRate } from '../../src/modules/tariff/entities/tariff-rate.entity';
import { DieselFloater } from '../../src/modules/tariff/entities/diesel-floater.entity';

// Import services
import { UploadService } from '../../src/modules/upload/upload.service';
import { TariffEngineService } from '../../src/modules/tariff/tariff-engine.service';

describe('MECU Dataset Validation', () => {
  let app: TestingModule;
  let uploadService: UploadService;
  let tariffEngineService: TariffEngineService;
  let uploadQueue: Queue;

  // Repositories
  let uploadRepository: Repository<Upload>;
  let shipmentRepository: Repository<Shipment>;
  let benchmarkRepository: Repository<ShipmentBenchmark>;
  let zoneMapRepository: Repository<TariffZoneMap>;
  let tariffTableRepository: Repository<TariffTable>;
  let tariffRateRepository: Repository<TariffRate>;
  let dieselFloaterRepository: Repository<DieselFloater>;
  let carrierAliasRepository: Repository<CarrierAlias>;

  // Test data IDs
  let testTenantId: string;
  let testCarrierId: string;
  let testTariffTableId: string;

  beforeAll(async () => {
    const moduleFixture: TestingModule = await Test.createTestingModule({
      imports: [
        ConfigModule.forRoot({
          isGlobal: true,
          envFilePath: '.env.test',
        }),
        DatabaseModule,
        BullModule.forRoot({
          redis: {
            host: process.env.REDIS_HOST || 'localhost',
            port: parseInt(process.env.REDIS_PORT || '6379'),
          },
        }),
        UploadModule,
        ParsingModule,
        TariffModule,
      ],
    }).compile();

    app = moduleFixture;
    uploadService = app.get<UploadService>(UploadService);
    tariffEngineService = app.get<TariffEngineService>(TariffEngineService);
    uploadQueue = app.get<Queue>(getQueueToken('upload'));

    // Get repositories
    uploadRepository = app.get<Repository<Upload>>(getRepositoryToken(Upload));
    shipmentRepository = app.get<Repository<Shipment>>(getRepositoryToken(Shipment));
    benchmarkRepository = app.get<Repository<ShipmentBenchmark>>(
      getRepositoryToken(ShipmentBenchmark)
    );
    zoneMapRepository = app.get<Repository<TariffZoneMap>>(getRepositoryToken(TariffZoneMap));
    tariffTableRepository = app.get<Repository<TariffTable>>(getRepositoryToken(TariffTable));
    tariffRateRepository = app.get<Repository<TariffRate>>(getRepositoryToken(TariffRate));
    dieselFloaterRepository = app.get<Repository<DieselFloater>>(getRepositoryToken(DieselFloater));
    carrierAliasRepository = app.get<Repository<CarrierAlias>>(getRepositoryToken(CarrierAlias));

    // Setup test data
    await setupTestData();
  });

  afterAll(async () => {
    // Clean up test data
    await cleanupTestData();

    // Close connections
    await uploadQueue.close();
    await app.close();
  });

  beforeEach(async () => {
    // Clear job queue before each test
    await uploadQueue.empty();
  });

  async function setupTestData(): Promise<void> {
    // Generate test IDs
    testTenantId = 'test-tenant-mecu';
    testCarrierId = 'test-carrier-cosi';
    testTariffTableId = 'test-tariff-table';

    // 1. Create test tenant (insert directly into database)
    await app.get('DataSource').query(
      `
      INSERT INTO tenant (id, name, settings, created_at)
      VALUES ($1, 'MECU Test', '{"currency": "EUR"}', NOW())
      ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
    `,
      [testTenantId]
    );

    // 2. Create test carrier
    await app.get('DataSource').query(
      `
      INSERT INTO carrier (id, name, code, contact_info, created_at)
      VALUES ($1, 'COSI Logistics', 'COSI', '{}', NOW())
      ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
    `,
      [testCarrierId]
    );

    // 3. Insert carrier alias
    await carrierAliasRepository.save({
      tenant_id: testTenantId,
      alias_text: 'COSI',
      carrier_id: testCarrierId,
    });

    // 4. Insert zone mapping: PLZ 60xxx → zone 3
    await zoneMapRepository.save({
      id: 'test-zone-map-60xxx',
      tenant_id: testTenantId,
      carrier_id: testCarrierId,
      origin_country: 'DE',
      origin_zip: '60*',
      destination_country: 'DE',
      destination_zip: '8*',
      zone: 3,
      created_at: new Date(),
    });

    // 5. Insert tariff table
    await tariffTableRepository.save({
      id: testTariffTableId,
      tenant_id: testTenantId,
      carrier_id: testCarrierId,
      name: 'COSI Test Tariff',
      lane_type: 'DE',
      currency: 'EUR',
      valid_from: new Date('2023-01-01'),
      valid_until: null,
      created_at: new Date(),
      rates: [],
    });

    // 6. Insert sample tariff for zone 3, 400-500kg: 294.30 EUR
    await tariffRateRepository.save({
      id: 'test-rate-zone3-400-500',
      tariff_table_id: testTariffTableId,
      zone: 3,
      weight_from_kg: 400,
      weight_to_kg: 500,
      rate_per_shipment: 294.3,
      rate_per_kg: null,
      tariff_table: null as any, // Will be populated by TypeORM
    });

    // 7. Insert diesel floater: 18.5% from 2023-01-01
    await dieselFloaterRepository.save({
      id: 'test-diesel-floater',
      tenant_id: testTenantId,
      carrier_id: testCarrierId,
      floater_pct: 18.5,
      basis: 'base',
      valid_from: new Date('2023-01-01'),
      valid_until: null,
    });

    console.log('Test data setup completed');
  }

  async function cleanupTestData(): Promise<void> {
    // Clean up in reverse order of dependencies
    await benchmarkRepository.delete({ tenant_id: testTenantId });
    await shipmentRepository.delete({ tenant_id: testTenantId });
    await uploadRepository.delete({ tenant_id: testTenantId });
    await tariffRateRepository.delete({ tariff_table_id: testTariffTableId });
    await tariffTableRepository.delete({ id: testTariffTableId });
    await dieselFloaterRepository.delete({ tenant_id: testTenantId });
    await zoneMapRepository.delete({ tenant_id: testTenantId });
    await carrierAliasRepository.delete({ tenant_id: testTenantId });

    // Clean up tenant and carrier from database
    await app.get('DataSource').query('DELETE FROM carrier WHERE id = $1', [testCarrierId]);
    await app.get('DataSource').query('DELETE FROM tenant WHERE id = $1', [testTenantId]);

    console.log('Test data cleanup completed');
  }

  async function waitForQueueProcessing(timeout = 10000): Promise<void> {
    return new Promise((resolve, reject) => {
      const startTime = Date.now();

      const checkQueue = async () => {
        const waiting = await uploadQueue.getWaiting();
        const active = await uploadQueue.getActive();

        if (waiting.length === 0 && active.length === 0) {
          resolve();
        } else if (Date.now() - startTime > timeout) {
          reject(new Error(`Queue processing timeout after ${timeout}ms`));
        } else {
          setTimeout(checkQueue, 100);
        }
      };

      checkQueue();
    });
  }

  describe('Test 1: CSV parsing and shipment creation', () => {
    it('should parse CSV and create shipments', async () => {
      // Read the sample CSV file
      const csvPath = path.join(__dirname, '..', 'fixtures', 'mecu', 'sample.csv');
      const csvContent = await fs.readFile(csvPath);

      // Create a mock file object
      const mockFile = {
        originalname: 'sample.csv',
        mimetype: 'text/csv',
        buffer: csvContent,
      } as Express.Multer.File;

      // Upload the file
      const result = await uploadService.uploadFile(mockFile, testTenantId, 'invoice');

      expect(result.upload).toBeDefined();
      expect(result.alreadyProcessed).toBe(false);

      // Wait for queue processing to complete
      await waitForQueueProcessing();

      // Assert: 10 shipments in database
      const shipments = await shipmentRepository.find({
        where: { tenant_id: testTenantId, upload_id: result.upload.id },
      });

      expect(shipments).toHaveLength(10);

      // Verify shipment details
      const firstShipment = shipments.find((s) => s.origin_zip === '60311');
      expect(firstShipment).toBeDefined();
      expect(firstShipment?.carrier_id).toBe(testCarrierId);
      expect(firstShipment?.weight_kg).toBe(450);
      expect(firstShipment?.actual_total_amount).toBe(348.75);
      expect(firstShipment?.currency).toBe('EUR');

      // Verify upload status
      const upload = await uploadRepository.findOne({
        where: { id: result.upload.id },
      });
      expect(upload?.status).toBe('parsed');
    });
  });

  describe('Test 2: Benchmark calculation validation', () => {
    it('should calculate benchmarks correctly', async () => {
      // Get shipments from previous test (or create new ones)
      const shipments = await shipmentRepository.find({
        where: { tenant_id: testTenantId },
      });

      expect(shipments.length).toBeGreaterThan(0);

      // Assert: all have shipment_benchmark records
      for (const shipment of shipments) {
        const benchmark = await benchmarkRepository.findOne({
          where: { shipment_id: shipment.id },
        });

        expect(benchmark).toBeDefined();
        expect(benchmark?.tenant_id).toBe(testTenantId);
      }

      // Assert: zone 3 / 450kg has expected_base_amount ≈ 294.30
      const testShipment = shipments.find((s) => s.weight_kg === 450 && s.origin_zip === '60311');
      expect(testShipment).toBeDefined();

      const testBenchmark = await benchmarkRepository.findOne({
        where: { shipment_id: testShipment!.id },
      });

      expect(testBenchmark).toBeDefined();
      expect(testBenchmark?.expected_base_amount).toBeCloseTo(294.3, 2);
      expect(testBenchmark?.expected_diesel_amount).toBeCloseTo(54.45, 2); // 18.5% of 294.30
      expect(testBenchmark?.expected_total_amount).toBeCloseTo(348.75, 2); // 294.30 + 54.45
      expect(testBenchmark?.classification).toBe('im_markt'); // Should be within market range
      expect(testBenchmark?.delta_amount).toBeCloseTo(0, 2); // 348.75 - 348.75 = 0
      expect(testBenchmark?.delta_pct).toBeCloseTo(0, 1);
    });
  });

  describe('Test 3: Overpay detection', () => {
    it('should detect overpay', async () => {
      // Create shipment with actual_total_amount = 500 EUR
      const testShipment = await shipmentRepository.save({
        tenant_id: testTenantId,
        upload_id: 'test-upload-overpay',
        carrier_id: testCarrierId,
        carrier_name: 'COSI',
        date: new Date('2023-01-15'),
        origin_zip: '60311',
        origin_country: 'DE',
        destination_zip: '80331',
        destination_country: 'DE',
        weight_kg: 450, // Same weight as base test
        length_m: 1.2,
        width_m: 0.8,
        height_m: 0.5,
        actual_total_amount: 500.0, // Overpaid amount
        currency: 'EUR',
        service_code: 'STANDARD',
        reference_number: 'TEST-OVERPAY-001',
      });

      // Calculate benchmark manually
      const benchmark = await tariffEngineService.calculateExpectedCost(testShipment);

      // Assert: classification = 'drüber'
      expect(benchmark.classification).toBe('drüber');

      // Assert: delta_amount > 100 EUR
      expect(benchmark.delta_amount).toBeGreaterThan(100);
      expect(benchmark.delta_amount).toBeCloseTo(151.25, 2); // 500 - 348.75 = 151.25

      // Assert: expected amounts are correct
      expect(benchmark.expected_base_amount).toBeCloseTo(294.3, 2);
      expect(benchmark.expected_diesel_amount).toBeCloseTo(54.45, 2);
      expect(benchmark.expected_total_amount).toBeCloseTo(348.75, 2);
      expect(benchmark.actual_total_amount).toBe(500.0);

      // Assert: percentage calculation
      const expectedDeltaPct = Math.round((151.25 / 348.75) * 100);
      expect(benchmark.delta_pct).toBeCloseTo(expectedDeltaPct, 0); // Should be ~43%
      expect(benchmark.delta_pct).toBeGreaterThan(5); // Confirms 'drüber' classification

      // Verify benchmark record was created
      const savedBenchmark = await benchmarkRepository.findOne({
        where: { shipment_id: testShipment.id },
      });

      expect(savedBenchmark).toBeDefined();
      expect(savedBenchmark?.classification).toBe('drüber');
      expect(savedBenchmark?.delta_amount).toBeCloseTo(151.25, 2);
    });

    it('should detect underpay', async () => {
      // Create shipment with actual_total_amount = 200 EUR (underpaid)
      const testShipment = await shipmentRepository.save({
        tenant_id: testTenantId,
        upload_id: 'test-upload-underpay',
        carrier_id: testCarrierId,
        carrier_name: 'COSI',
        date: new Date('2023-01-15'),
        origin_zip: '60317',
        origin_country: 'DE',
        destination_zip: '80337',
        destination_country: 'DE',
        weight_kg: 430,
        length_m: 1.1,
        width_m: 0.7,
        height_m: 0.5,
        actual_total_amount: 200.0, // Underpaid amount
        currency: 'EUR',
        service_code: 'STANDARD',
        reference_number: 'TEST-UNDERPAY-001',
      });

      // Calculate benchmark manually
      const benchmark = await tariffEngineService.calculateExpectedCost(testShipment);

      // Assert: classification = 'unter'
      expect(benchmark.classification).toBe('unter');

      // Assert: negative delta amount
      expect(benchmark.delta_amount).toBeLessThan(-100);

      // Assert: percentage is less than -5%
      expect(benchmark.delta_pct).toBeLessThan(-5);
    });
  });
});
