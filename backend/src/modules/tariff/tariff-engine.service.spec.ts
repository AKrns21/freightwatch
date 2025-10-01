import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { NotFoundException } from '@nestjs/common';
import { TariffEngineService } from './tariff-engine.service';
import { TariffTable } from './entities/tariff-table.entity';
import { TariffRate } from './entities/tariff-rate.entity';
// import { TariffRule } from './entities/tariff-rule.entity'; // TODO: Create entity
import { DieselFloater } from './entities/diesel-floater.entity';
import { ShipmentBenchmark } from './entities/shipment-benchmark.entity';
import { ZoneCalculatorService } from './zone-calculator.service';
import { FxService } from './fx.service';
import { Shipment } from '../parsing/entities/shipment.entity';

describe('TariffEngineService', () => {
  let service: TariffEngineService;
  let tariffTableRepository: jest.Mocked<Repository<TariffTable>>;
  let tariffRateRepository: jest.Mocked<Repository<TariffRate>>;
  let tariffRuleRepository: jest.Mocked<Repository<any>>;
  let dieselFloaterRepository: jest.Mocked<Repository<DieselFloater>>;
  let shipmentBenchmarkRepository: jest.Mocked<Repository<ShipmentBenchmark>>;
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
          provide: getRepositoryToken('TariffRule' as any),
          useValue: {
            find: jest.fn(),
          },
        },
        {
          provide: getRepositoryToken(DieselFloater),
          useValue: {
            findOne: jest.fn(),
          },
        },
        {
          provide: getRepositoryToken(ShipmentBenchmark),
          useValue: {
            save: jest.fn(),
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
    tariffRuleRepository = module.get(getRepositoryToken('TariffRule' as any));
    dieselFloaterRepository = module.get(getRepositoryToken(DieselFloater));
    shipmentBenchmarkRepository = module.get(getRepositoryToken(ShipmentBenchmark));
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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark); // No chargeable weight rules

      const result = await service.calculateExpectedCost(mockShipment as Shipment);

      const expectedTollAmount = 0; // Under 3.5t threshold  
      const expectedDieselAmount = Math.round(294.30 * (18.5 / 100) * 100) / 100; // 54.45
      const expectedTotal = 294.30 + expectedTollAmount + expectedDieselAmount; // 348.75

      expect(result.expected_base_amount).toBe(294.30);
      expect(result.expected_toll_amount).toBe(expectedTollAmount);
      expect(result.expected_diesel_amount).toBe(expectedDieselAmount);
      expect(result.expected_total_amount).toBe(expectedTotal);
      expect(result.cost_breakdown).toHaveLength(3);
      expect(result.cost_breakdown[0]).toEqual({
        item: 'base_rate',
        description: 'Zone 3 base rate (kg)',
        zone: 3,
        weight: 450,
        rate: 294.30,
        amount: 294.30,
        currency: 'EUR',
        note: 'Using actual weight: 450kg',
      });
      expect(result.cost_breakdown[1]).toEqual({
        item: 'toll',
        description: 'Toll charges (estimated_heuristic)',
        value: expectedTollAmount,
        amount: expectedTollAmount,
        currency: 'EUR',
        note: 'estimated_heuristic',
      });
      expect(result.cost_breakdown[2]).toEqual({
        item: 'diesel_surcharge',
        description: 'Diesel surcharge (18.5% on base)',
        base: 294.30,
        pct: 18.5,
        value: expectedDieselAmount,
        amount: expectedDieselAmount,
        currency: 'EUR',
      });
      expect(result.calculation_metadata.tariff_table_id).toBe('tariff-123');
      expect(result.calculation_metadata.lane_type).toBe('DE');
      expect(result.calculation_metadata.zone_calculated).toBe(3);
      expect(result.calculation_metadata.calc_version).toBe('1.4-complete-benchmark');
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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);
      fxService.getRate.mockResolvedValue(0.9850); // EUR to CHF

      const result = await service.calculateExpectedCost(chfShipment as Shipment);

      expect(fxService.getRate).toHaveBeenCalledWith('EUR', 'CHF', testDate);
      expect(result.expected_base_amount).toBe(289.89); // 294.30 * 0.9850
      expect(result.cost_breakdown[0].currency).toBe('CHF');
      expect(result.cost_breakdown[0].note).toBe('Using actual weight: 450kg. Converted from EUR using rate 0.985');
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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

      const result = await service.calculateExpectedCost(mockShipment as Shipment);

      expect(result.calculation_metadata.zone_calculated).toBe(1); // Fallback zone
      expect(result.expected_base_amount).toBe(250.00);
    });

    it('should throw error when no applicable tariff found', async () => {
      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(null);
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);
      fxService.getRate.mockRejectedValue(new Error('FX rate not found'));

      const result = await service.calculateExpectedCost(chfShipment as Shipment);

      expect(result.expected_base_amount).toBe(294.30); // Original amount
      expect(result.cost_breakdown[0].note).toBe('Using actual weight: 450kg. Conversion failed, using original EUR amount');
      expect(result.calculation_metadata.fx_rate_used).toBeUndefined();
    });

    it('should apply LDM conversion rule when length is higher weight', async () => {
      const ldmShipment = {
        ...mockShipment,
        weight_kg: 300,
        length_m: 2.5,
      };

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
        zone: 3,
        weight_from_kg: 4000,
        weight_to_kg: 5000,
        rate_per_shipment: 2500.00,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      const mockLdmRule: any = {
        id: 'rule-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        rule_type: 'ldm_conversion',
        param_json: { ldm_to_kg: 1850 },
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      tariffRuleRepository.find.mockResolvedValue([mockLdmRule]);

      const result = await service.calculateExpectedCost(ldmShipment as Shipment);

      expect(result.expected_base_amount).toBe(2500.00);
      expect(result.cost_breakdown[0].description).toBe('Zone 3 base rate (lm)');
      expect(result.cost_breakdown[0].weight).toBe(4625); // 2.5 × 1850
      expect(result.cost_breakdown[0].note).toBe('LDM weight: 2.5m × 1850kg/m = 4625kg');
      expect(result.calculation_metadata.calc_version).toBe('1.4-complete-benchmark');
    });

    it('should apply minimum pallet weight rule when pallet weight is higher', async () => {
      const palletShipment = {
        ...mockShipment,
        weight_kg: 200,
        pallets: 3,
      };

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
        zone: 3,
        weight_from_kg: 650,
        weight_to_kg: 750,
        rate_per_shipment: 400.00,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      const mockPalletRule: any = {
        id: 'rule-456',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        rule_type: 'min_pallet_weight',
        param_json: { min_weight_per_pallet_kg: 250 },
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      tariffRuleRepository.find.mockResolvedValue([mockPalletRule]);

      const result = await service.calculateExpectedCost(palletShipment as Shipment);

      expect(result.expected_base_amount).toBe(400.00);
      expect(result.cost_breakdown[0].description).toBe('Zone 3 base rate (pallet)');
      expect(result.cost_breakdown[0].weight).toBe(750); // 3 × 250
      expect(result.cost_breakdown[0].note).toBe('Pallet weight: 3 × 250kg/pallet = 750kg');
    });

    it('should use actual weight when chargeable weight rules result in lower weight', async () => {
      const heavyShipment = {
        ...mockShipment,
        weight_kg: 800,
        length_m: 0.3, // LDM weight would be 555kg (0.3 × 1850)
        pallets: 2,     // Pallet weight would be 500kg (2 × 250)
      };

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
        zone: 3,
        weight_from_kg: 750,
        weight_to_kg: 850,
        rate_per_shipment: 500.00,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      const mockRules: any[] = [
        {
          id: 'rule-123',
          tenant_id: mockTenantId,
          carrier_id: mockCarrierId,
          rule_type: 'ldm_conversion',
          param_json: { ldm_to_kg: 1850 },
          valid_from: new Date('2023-01-01'),
          valid_until: null,
        },
        {
          id: 'rule-456',
          tenant_id: mockTenantId,
          carrier_id: mockCarrierId,
          rule_type: 'min_pallet_weight',
          param_json: { min_weight_per_pallet_kg: 250 },
          valid_from: new Date('2023-01-01'),
          valid_until: null,
        },
      ];

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      tariffRuleRepository.find.mockResolvedValue(mockRules);

      const result = await service.calculateExpectedCost(heavyShipment as Shipment);

      expect(result.expected_base_amount).toBe(500.00);
      expect(result.cost_breakdown[0].description).toBe('Zone 3 base rate (kg)');
      expect(result.cost_breakdown[0].weight).toBe(800); // Using actual weight
      expect(result.cost_breakdown[0].note).toBe('LDM weight 555kg < actual weight, using actual; Pallet weight 500kg < chargeable weight, using current');
    });

    it('should calculate diesel surcharge correctly', async () => {
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
        zone: 3,
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: 294.30,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      const mockDieselFloater: DieselFloater = {
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue(mockDieselFloater);

      const result = await service.calculateExpectedCost(mockShipment as Shipment);

      // Base: 294.30 EUR, Toll: 0 EUR (under threshold), Diesel: 18.5% = 54.45 EUR (rounded), Total: 348.75 EUR
      expect(result.expected_base_amount).toBe(294.30);
      expect(result.expected_toll_amount).toBe(0);
      expect(result.expected_diesel_amount).toBe(54.45);
      expect(result.expected_total_amount).toBe(348.75);
      expect(result.cost_breakdown).toHaveLength(3);
      expect(result.cost_breakdown[1]).toEqual({
        item: 'toll',
        description: 'Toll charges (estimated_heuristic)',
        value: 0,
        amount: 0,
        currency: 'EUR',
        note: 'estimated_heuristic',
      });
      expect(result.cost_breakdown[2]).toEqual({
        item: 'diesel_surcharge',
        description: 'Diesel surcharge (18.5% on base)',
        base: 294.30,
        pct: 18.5,
        value: 54.45,
        amount: 54.45,
        currency: 'EUR',
      });
      expect(result.calculation_metadata.calc_version).toBe('1.4-complete-benchmark');
    });

    it('should use default diesel floater when none found', async () => {
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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue(null); // No diesel floater found

      const result = await service.calculateExpectedCost(mockShipment as Shipment);

      // Should use default 18.5% fallback
      expect(result.expected_diesel_amount).toBe(54.45); // 294.30 * 0.185 = 54.45
      expect(result.cost_breakdown[2]).toEqual({
        item: 'diesel_surcharge',
        description: 'Diesel surcharge (18.5% on base)',
        base: 294.30,
        pct: 18.5,
        value: 54.45,
        amount: 54.45,
        currency: 'EUR',
      });
    });

    it('should use invoice toll amount when provided', async () => {
      const shipmentWithToll = {
        ...mockShipment,
        toll_amount: 15.50,
      };

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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

      const result = await service.calculateExpectedCost(shipmentWithToll as Shipment);

      expect(result.expected_toll_amount).toBe(15.50);
      expect(result.cost_breakdown[1]).toEqual({
        item: 'toll',
        description: 'Toll charges (from_invoice)',
        value: 15.50,
        amount: 15.50,
        currency: 'EUR',
        note: 'from_invoice',
      });
    });

    it('should estimate toll for heavy shipments', async () => {
      const heavyShipment = {
        ...mockShipment,
        weight_kg: 4000, // Above 3.5t threshold
        dest_country: 'DE',
      };

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
        zone: 3,
        weight_from_kg: 3500,
        weight_to_kg: 4500,
        rate_per_shipment: 600.00,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

      const result = await service.calculateExpectedCost(heavyShipment as Shipment);

      // Zone 3 DE toll should be 12 EUR
      expect(result.expected_toll_amount).toBe(12);
      expect(result.cost_breakdown[1]).toEqual({
        item: 'toll',
        description: 'Toll charges (estimated_heuristic)',
        value: 12,
        amount: 12,
        currency: 'EUR',
        note: 'estimated_heuristic',
      });
    });

    it('should calculate diesel surcharge on base_plus_toll', async () => {
      const heavyShipment = {
        ...mockShipment,
        weight_kg: 4000, // Above 3.5t threshold
        dest_country: 'DE',
      };

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
        zone: 3,
        weight_from_kg: 3500,
        weight_to_kg: 4500,
        rate_per_shipment: 300.00,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      const mockDieselFloater: DieselFloater = {
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base_plus_toll', // Key difference
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue(mockDieselFloater);

      const result = await service.calculateExpectedCost(heavyShipment as Shipment);

      // Base: 300.00, Toll: 12.00, Diesel base: 312.00 (base + toll)
      // Diesel: 312.00 * 0.185 = 57.72, Total: 300 + 12 + 57.72 = 369.72
      const expectedBase = 300.00;
      const expectedToll = 12.00;
      const expectedDieselBase = expectedBase + expectedToll; // 312.00
      const expectedDiesel = Math.round(expectedDieselBase * 0.185 * 100) / 100; // 57.72
      const expectedTotal = expectedBase + expectedToll + expectedDiesel;

      expect(result.expected_base_amount).toBe(expectedBase);
      expect(result.expected_toll_amount).toBe(expectedToll);
      expect(result.expected_diesel_amount).toBe(expectedDiesel);
      expect(result.expected_total_amount).toBe(expectedTotal);
      expect(result.cost_breakdown[2]).toEqual({
        item: 'diesel_surcharge',
        description: 'Diesel surcharge (18.5% on base_plus_toll)',
        base: expectedDieselBase,
        pct: 18.5,
        value: expectedDiesel,
        amount: expectedDiesel,
        currency: 'EUR',
      });
    });

    it('should complete full benchmark flow with delta calculation', async () => {
      const fullShipment = {
        ...mockShipment,
        id: 'shipment-full-test',
        actual_total_amount: 348.75, // Matches expected total for 'im_markt' classification
      };

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
        zone: 3,
        weight_from_kg: 400,
        weight_to_kg: 500,
        rate_per_shipment: 294.30,
        rate_per_kg: null,
        tariff_table: mockTariffTable,
      };

      const mockDieselFloater: DieselFloater = {
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      };

      zoneCalculatorService.calculateZone.mockResolvedValue(3);
      tariffTableRepository.findOne.mockResolvedValue(mockTariffTable);
      tariffRateRepository.findOne.mockResolvedValue(mockTariffRate);
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue(mockDieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

      const result = await service.calculateExpectedCost(fullShipment as Shipment);

      // Verify full benchmark calculation
      expect(result.expected_base_amount).toBe(294.30);
      expect(result.expected_toll_amount).toBe(0);
      expect(result.expected_diesel_amount).toBe(54.45);
      expect(result.expected_total_amount).toBe(348.75);
      expect(result.actual_total_amount).toBe(348.75);
      expect(result.delta_amount).toBe(0); // 348.75 - 348.75 = 0
      expect(result.delta_pct).toBe(0); // 0/348.75 * 100 = 0%
      expect(result.classification).toBe('im_markt'); // 0% is between -5% and 5%

      // Verify cost breakdown
      expect(result.cost_breakdown).toHaveLength(3);
      expect(result.cost_breakdown[0].item).toBe('base_rate');
      expect(result.cost_breakdown[1].item).toBe('toll');
      expect(result.cost_breakdown[2].item).toBe('diesel_surcharge');

      // Verify calculation metadata includes diesel info
      expect(result.calculation_metadata.diesel_basis_used).toBe('base');
      expect(result.calculation_metadata.diesel_pct_used).toBe(18.5);
      expect(result.calculation_metadata.calc_version).toBe('1.4-complete-benchmark');

      // Verify no reporting currency conversion (shipment currency matches tenant EUR)
      expect(result.report_amounts).toBeNull();

      // Verify benchmark record was saved
      expect(shipmentBenchmarkRepository.save).toHaveBeenCalledWith(
        expect.objectContaining({
          shipment_id: 'shipment-full-test',
          tenant_id: mockTenantId,
          expected_base_amount: 294.30,
          expected_toll_amount: null,
          expected_diesel_amount: 54.45,
          expected_total_amount: 348.75,
          actual_total_amount: 348.75,
          delta_amount: 0,
          delta_pct: 0,
          classification: 'im_markt',
          currency: 'EUR',
          report_currency: null,
          diesel_basis_used: 'base',
          diesel_pct_used: 18.5,
        }),
      );
    });

    it('should classify shipment as "drüber" when actual > expected + 5%', async () => {
      const expensiveShipment = {
        ...mockShipment,
        id: 'shipment-expensive',
        actual_total_amount: 400.00, // Expected: 348.75, Delta: +51.25 EUR (+14.7%)
      };

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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

      const result = await service.calculateExpectedCost(expensiveShipment as Shipment);

      expect(result.expected_total_amount).toBe(348.75);
      expect(result.actual_total_amount).toBe(400.00);
      expect(result.delta_amount).toBe(51.25); // 400.00 - 348.75
      expect(result.delta_pct).toBe(14.7); // round((51.25 / 348.75) * 100)
      expect(result.classification).toBe('drüber'); // 14.69% > 5%
    });

    it('should classify shipment as "unter" when actual < expected - 5%', async () => {
      const cheapShipment = {
        ...mockShipment,
        id: 'shipment-cheap',
        actual_total_amount: 300.00, // Expected: 348.75, Delta: -48.75 EUR (-13.98%)
      };

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
      tariffRuleRepository.find.mockResolvedValue([]);
      dieselFloaterRepository.findOne.mockResolvedValue({
        id: 'diesel-123',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        floater_pct: 18.5,
        basis: 'base',
        valid_from: new Date('2023-01-01'),
        valid_until: null,
      } as DieselFloater);
      shipmentBenchmarkRepository.save.mockResolvedValue({} as ShipmentBenchmark);

      const result = await service.calculateExpectedCost(cheapShipment as Shipment);

      expect(result.expected_total_amount).toBe(348.75);
      expect(result.actual_total_amount).toBe(300.00);
      expect(result.delta_amount).toBe(-48.75); // 300.00 - 348.75
      expect(result.delta_pct).toBe(-13.98); // round((-48.75 / 348.75) * 100)
      expect(result.classification).toBe('unter'); // -13.98% < -5%
    });
  });
});