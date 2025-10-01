import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { NotFoundException } from '@nestjs/common';
import { TariffEngineService } from './tariff-engine.service';
import { TariffTable } from './entities/tariff-table.entity';
import { TariffRate } from './entities/tariff-rate.entity';
import { ZoneCalculatorService } from './zone-calculator.service';
import { FxService } from './fx.service';
import { Shipment } from '../parsing/entities/shipment.entity';

describe('TariffEngineService', () => {
  let service: TariffEngineService;
  let tariffTableRepository: jest.Mocked<Repository<TariffTable>>;
  let tariffRateRepository: jest.Mocked<Repository<TariffRate>>;
  let zoneCalculatorService: jest.Mocked<ZoneCalculatorService>;
  let fxService: jest.Mocked<FxService>;

  const mockTenantId = 'tenant-123';
  const mockCarrierId = 'carrier-456';
  const testDate = new Date('2023-01-15');

  const mockShipment: Partial<Shipment> = {
    id: 'shipment-789',
    tenant_id: mockTenantId,
    carrier_id: mockCarrierId,
    date: testDate,
    origin_country: 'DE',
    dest_country: 'DE',
    dest_zip: '80331',
    weight_kg: 450,
    currency: 'EUR',
  };

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        TariffEngineService,
        {
          provide: getRepositoryToken(TariffTable),
          useValue: {
            findOne: jest.fn(),
          },
        },
        {
          provide: getRepositoryToken(TariffRate),
          useValue: {
            findOne: jest.fn(),
          },
        },
        {
          provide: ZoneCalculatorService,
          useValue: {
            calculateZone: jest.fn(),
          },
        },
        {
          provide: FxService,
          useValue: {
            getRate: jest.fn(),
          },
        },
      ],
    }).compile();

    service = module.get<TariffEngineService>(TariffEngineService);
    tariffTableRepository = module.get(getRepositoryToken(TariffTable));
    tariffRateRepository = module.get(getRepositoryToken(TariffRate));
    zoneCalculatorService = module.get(ZoneCalculatorService);
    fxService = module.get(FxService);
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should be defined', () => {
    expect(service).toBeDefined();
  });

  describe('calculateExpectedCost', () => {
    it('should calculate base cost for DE domestic shipment', async () => {
      const mockTariffTable: TariffTable = {
        id: 'tariff-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        name: 'DE Standard Tariff',
        lane_type: 'DE',
        currency: 'EUR',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
        created_at: new Date(),
        rates: [],
      };

      const mockTariffRate: TariffRate = {
        id: 'rate-456',
        tariff_table_id: 'tariff-123',
        zone: 3,
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: 294.30,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);

      const result = await service.calculateExpectedCost(mockShipment as Shipment);

      expect(result.expected_base_amount).toBe(294.30);
      expect(result.expected_total_amount).toBe(294.30);
      expect(result.cost_breakdown).toHaveLength(1);
      expect(result.cost_breakdown[0]).toEqual({
        item: 'base_rate',
        description: 'Zone 3 base rate',
        zone: 3,
        weight: 450,
        rate: 294.30,
        amount: 294.30,
        currency: 'EUR',
        note: undefined,
      });
      expect(result.calculation_metadata.tariff_table_id).toBe('tariff-123');
      expect(result.calculation_metadata.lane_type).toBe('DE');
      expect(result.calculation_metadata.zone_calculated).toBe(3);
      expect(result.calculation_metadata.calc_version).toBe('1.0-base-only');
    });

    it('should determine correct lane types', async () => {
      const testCases = [
        { origin: 'DE', dest: 'DE', expected: 'DE' },
        { origin: 'DE', dest: 'AT', expected: 'AT' },
        { origin: 'AT', dest: 'DE', expected: 'AT' },
        { origin: 'DE', dest: 'CH', expected: 'CH' },
        { origin: 'CH', dest: 'DE', expected: 'CH' },
        { origin: 'DE', dest: 'FR', expected: 'EU' },
        { origin: 'FR', dest: 'IT', expected: 'EU' },
        { origin: 'DE', dest: 'US', expected: 'EXPORT' },
      ];

      const mockTariffTable: TariffTable = {
        id: 'tariff-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        name: 'Test Tariff',
        lane_type: 'DE', // Will be overridden in each test
        currency: 'EUR',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
        created_at: new Date(),
        rates: [],
      };

      const mockTariffRate: TariffRate = {
        id: 'rate-456',
        tariff_table_id: 'tariff-123',
        zone: 3,
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: 294.30,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);

      for (const testCase of testCases) {
        const testShipment = {
          ...mockShipment,
          origin_country: testCase.origin,
          dest_country: testCase.dest,
        };

        // Mock the tariff table with expected lane type
        const expectedTariff = { ...mockTariffTable, lane_type: testCase.expected };
        tariffTableRepository.findOne.mockResolvedValue(expectedTariff);

        await service.calculateExpectedCost(testShipment as Shipment);

        expect(tariffTableRepository.findOne).toHaveBeenCalledWith({
          where: {
            tenant_id: mockTenantId,
            carrier_id: mockCarrierId,
            lane_type: testCase.expected,
            valid_from: expect.any(Object),
            valid_until: expect.any(Object),
          },
          order: {
            valid_from: 'DESC',
          },
        });

        tariffTableRepository.findOne.mockClear();
      }
    });

    it('should handle rate_per_kg tariffs', async () => {
      const mockTariffTable: TariffTable = {
        id: 'tariff-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        name: 'Per-KG Tariff',
        lane_type: 'DE',
        currency: 'EUR',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
        created_at: new Date(),
        rates: [],
      };

      const mockTariffRate: TariffRate = {
        id: 'rate-456',
        tariff_table_id: 'tariff-123',
        zone: 3,
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: null,
        rate_per_kg: 0.65, // 450kg * 0.65 = 292.50
        tariff_table: mockTariffTable,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);

      const result = await service.calculateExpectedCost(mockShipment as Shipment);

      expect(result.expected_base_amount).toBe(292.50);
      expect(result.cost_breakdown[0].rate).toBe(0.65);
      expect(result.cost_breakdown[0].amount).toBe(292.50);
    });

    it('should handle currency conversion', async () => {
      const chfShipment = {
        ...mockShipment,
        currency: 'CHF',
      };

      const mockTariffTable: TariffTable = {
        id: 'tariff-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        name: 'EUR Tariff',
        lane_type: 'DE',
        currency: 'EUR', // Tariff in EUR
        valid_from: new Date('2023-01-01'),
        valid_until: null,
        created_at: new Date(),
        rates: [],
      };

      const mockTariffRate: TariffRate = {
        id: 'rate-456',
        tariff_table_id: 'tariff-123',
        zone: 3,
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: 294.30,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      fxService.getRate.mockResolvedValue(0.9850); // EUR to CHF

      const result = await service.calculateExpectedCost(chfShipment as Shipment);

      expect(fxService.getRate).toHaveBeenCalledWith('EUR', 'CHF', testDate);
      expect(result.expected_base_amount).toBe(289.89); // 294.30 * 0.9850
      expect(result.cost_breakdown[0].currency).toBe('CHF');
      expect(result.cost_breakdown[0].note).toBe('Converted from EUR using rate 0.985');
      expect(result.calculation_metadata.fx_rate_used).toBe(0.9850);
    });

    it('should use fallback zone when zone calculation fails', async () => {
      const mockTariffTable: TariffTable = {
        id: 'tariff-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        name: 'DE Tariff',
        lane_type: 'DE',
        currency: 'EUR',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
        created_at: new Date(),
        rates: [],
      };

      const mockTariffRate: TariffRate = {
        id: 'rate-456',
        tariff_table_id: 'tariff-123',
        zone: 1, // Fallback zone for DE
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: 250.00,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      zoneCalculatorService.calculateZone.mockRejectedValue(new Error('Zone not found'));
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);

      const result = await service.calculateExpectedCost(mockShipment as Shipment);

      expect(result.calculation_metadata.zone_calculated).toBe(1); // Fallback zone
      expect(result.expected_base_amount).toBe(250.00);
    });

    it('should throw error when no applicable tariff found', async () => {
      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(null);

      await expect(
        service.calculateExpectedCost(mockShipment as Shipment)
      ).rejects.toThrow(NotFoundException);
    });

    it('should throw error when no tariff rate found', async () => {
      const mockTariffTable: TariffTable = {
        id: 'tariff-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        name: 'Test Tariff',
        lane_type: 'DE',
        currency: 'EUR',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
        created_at: new Date(),
        rates: [],
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(null);

      await expect(
        service.calculateExpectedCost(mockShipment as Shipment)
      ).rejects.toThrow(NotFoundException);
    });

    it('should handle FX conversion failure gracefully', async () => {
      const chfShipment = {
        ...mockShipment,
        currency: 'CHF',
      };

      const mockTariffTable: TariffTable = {
        id: 'tariff-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        name: 'EUR Tariff',
        lane_type: 'DE',
        currency: 'EUR',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
        created_at: new Date(),
        rates: [],
      };

      const mockTariffRate: TariffRate = {
        id: 'rate-456',
        tariff_table_id: 'tariff-123',
        zone: 3,
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: 294.30,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      fxService.getRate.mockRejectedValue(new Error('FX rate not found'));

      const result = await service.calculateExpectedCost(chfShipment as Shipment);

      expect(result.expected_base_amount).toBe(294.30); // Original amount
      expect(result.cost_breakdown[0].note).toBe('Conversion failed, using original EUR amount');
      expect(result.calculation_metadata.fx_rate_used).toBeUndefined();
    });
  });
});