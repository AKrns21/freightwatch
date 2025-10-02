import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { NotFoundException } from '@nestjs/common';
import { ZoneCalculatorService } from './zone-calculator.service';
import { TariffZoneMap } from './entities/tariff-zone-map.entity';

describe('ZoneCalculatorService', () => {
  let service: ZoneCalculatorService;
  let tariffZoneMapRepository: jest.Mocked<Repository<TariffZoneMap>>;

  const mockTenantId = 'tenant-123';
  const mockCarrierId = 'carrier-456';
  const testDate = new Date('2024-03-01');

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        ZoneCalculatorService,
        {
          provide: getRepositoryToken(TariffZoneMap),
          useValue: {
            findOne: jest.fn(),
            find: jest.fn(),
          },
        },
      ],
    }).compile();

    service = module.get<ZoneCalculatorService>(ZoneCalculatorService);
    tariffZoneMapRepository = module.get(getRepositoryToken(TariffZoneMap));
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should be defined', () => {
    expect(service).toBeDefined();
  });

  describe('calculateZone', () => {
    it('should throw error for empty destination ZIP', async () => {
      await expect(
        service.calculateZone(mockTenantId, mockCarrierId, 'DE', '', testDate)
      ).rejects.toThrow('Destination ZIP code is required for zone calculation');
    });

    it('should calculate zone using 2-digit prefix matching', async () => {
      const mockMapping: Partial<TariffZoneMap> = {
        id: '1',
        tenant_id: mockTenantId,
        carrier_id: mockCarrierId,
        country: 'DE',
        plz_prefix: '42',
        prefix_len: 2,
        pattern: undefined,
        zone: 1,
        valid_from: new Date('2024-01-01'),
        valid_until: undefined,
      };

      tariffZoneMapRepository.findOne.mockResolvedValue(mockMapping as TariffZoneMap);

      const result = await service.calculateZone(
        mockTenantId,
        mockCarrierId,
        'DE',
        '42349',
        testDate
      );

      expect(result).toBe(1);
      expect(tariffZoneMapRepository.findOne).toHaveBeenCalledWith({
        where: {
          tenant_id: mockTenantId,
          carrier_id: mockCarrierId,
          country: 'DE',
          plz_prefix: '42349',
          prefix_len: 5,
          valid_from: expect.any(Object), // LessThanOrEqual
          valid_until: expect.any(Object), // Or condition
        },
        order: {
          valid_from: 'DESC',
        },
      });
    });

    it('should try decreasing prefix lengths until match found', async () => {
      tariffZoneMapRepository.findOne
        .mockResolvedValueOnce(null) // 5-digit fails
        .mockResolvedValueOnce(null) // 4-digit fails
        .mockResolvedValueOnce(null) // 3-digit fails
        .mockResolvedValueOnce({    // 2-digit succeeds
          zone: 4,
          plz_prefix: '78',
          prefix_len: 2,
        } as TariffZoneMap);

      const result = await service.calculateZone(
        mockTenantId,
        mockCarrierId,
        'DE',
        '78234',
        testDate
      );

      expect(result).toBe(4);
      expect(tariffZoneMapRepository.findOne).toHaveBeenCalledTimes(4);
    });

    it('should fallback to pattern matching when prefix fails', async () => {
      tariffZoneMapRepository.findOne.mockResolvedValue(null); // All prefix attempts fail

      const mockPatternMappings = [
        {
          id: '1',
          pattern: '^SW\\d[A-Z]\\s\\d[A-Z]{2}$', // UK postcode pattern
          zone: 5,
        } as TariffZoneMap,
      ];

      tariffZoneMapRepository.find.mockResolvedValue(mockPatternMappings);

      const result = await service.calculateZone(
        mockTenantId,
        mockCarrierId,
        'UK',
        'SW1A 1AA',
        testDate
      );

      expect(result).toBe(5);
      expect(tariffZoneMapRepository.find).toHaveBeenCalledWith({
        where: {
          tenant_id: mockTenantId,
          carrier_id: mockCarrierId,
          country: 'UK',
          pattern: expect.any(Object), // Not(IsNull())
          valid_from: expect.any(Object),
          valid_until: expect.any(Object),
        },
        order: {
          valid_from: 'DESC',
        },
      });
    });

    it('should handle multiple pattern mappings and return first match', async () => {
      tariffZoneMapRepository.findOne.mockResolvedValue(null);

      const mockPatternMappings = [
        {
          pattern: '^\\d{4}$', // 4-digit pattern
          zone: 3,
        },
        {
          pattern: '^SW.*', // London SW pattern
          zone: 5,
        },
      ] as TariffZoneMap[];

      tariffZoneMapRepository.find.mockResolvedValue(mockPatternMappings);

      const result = await service.calculateZone(
        mockTenantId,
        mockCarrierId,
        'UK',
        'SW1A',
        testDate
      );

      expect(result).toBe(5); // Should match SW pattern, not 4-digit
    });

    it('should handle invalid regex patterns gracefully', async () => {
      tariffZoneMapRepository.findOne.mockResolvedValue(null);

      const mockPatternMappings = [
        {
          pattern: '[invalid regex', // Invalid regex
          zone: 1,
        },
        {
          pattern: '^42.*', // Valid regex
          zone: 2,
        },
      ] as TariffZoneMap[];

      tariffZoneMapRepository.find.mockResolvedValue(mockPatternMappings);

      const result = await service.calculateZone(
        mockTenantId,
        mockCarrierId,
        'DE',
        '42123',
        testDate
      );

      expect(result).toBe(2); // Should skip invalid regex and use valid one
    });

    it('should throw NotFoundException when no mapping found', async () => {
      tariffZoneMapRepository.findOne.mockResolvedValue(null);
      tariffZoneMapRepository.find.mockResolvedValue([]);

      await expect(
        service.calculateZone(mockTenantId, mockCarrierId, 'DE', '99999', testDate)
      ).rejects.toThrow(NotFoundException);
    });

    it('should normalize country and zip codes', async () => {
      const mockMapping = {
        zone: 1,
      } as TariffZoneMap;

      tariffZoneMapRepository.findOne.mockResolvedValue(mockMapping);

      await service.calculateZone(
        mockTenantId,
        mockCarrierId,
        'de', // lowercase country
        ' 42349 ', // ZIP with spaces
        testDate
      );

      expect(tariffZoneMapRepository.findOne).toHaveBeenCalledWith(
        expect.objectContaining({
          where: expect.objectContaining({
            country: 'DE',
            plz_prefix: '42349',
          }),
        })
      );
    });

    it('should respect date range filters', async () => {
      const futureDate = new Date('2025-01-01');
      
      tariffZoneMapRepository.findOne.mockResolvedValue(null);
      tariffZoneMapRepository.find.mockResolvedValue([]);

      await expect(
        service.calculateZone(mockTenantId, mockCarrierId, 'DE', '42349', futureDate)
      ).rejects.toThrow(NotFoundException);

      // Verify date filters are applied
      expect(tariffZoneMapRepository.findOne).toHaveBeenCalledWith(
        expect.objectContaining({
          where: expect.objectContaining({
            valid_from: expect.any(Object),
            valid_until: expect.any(Object),
          }),
        })
      );
    });
  });

  describe('bulkCalculateZones', () => {
    it('should calculate multiple zones efficiently with caching', async () => {
      const requests = [
        { country: 'DE', destZip: '42349', date: testDate },
        { country: 'DE', destZip: '42350', date: testDate },
        { country: 'DE', destZip: '42349', date: testDate }, // Duplicate for caching
      ];

      tariffZoneMapRepository.findOne
        .mockResolvedValueOnce({ zone: 1 } as TariffZoneMap) // First call for 42349
        .mockResolvedValueOnce({ zone: 1 } as TariffZoneMap); // Second call for 42350

      const results = await service.bulkCalculateZones(
        mockTenantId,
        mockCarrierId,
        requests
      );

      expect(results.size).toBe(2); // Should deduplicate the duplicate request
      expect(results.get('DE-42349-Fri Mar 01 2024')).toBe(1);
      expect(results.get('DE-42350-Fri Mar 01 2024')).toBe(1);
      
      // Should only call repository twice due to caching
      expect(tariffZoneMapRepository.findOne).toHaveBeenCalledTimes(2);
    });

    it('should continue processing even when some zones fail', async () => {
      const requests = [
        { country: 'DE', destZip: '42349', date: testDate },
        { country: 'DE', destZip: '99999', date: testDate }, // Will fail
        { country: 'DE', destZip: '78234', date: testDate },
      ];

      tariffZoneMapRepository.findOne
        .mockResolvedValueOnce({ zone: 1 } as TariffZoneMap)
        .mockResolvedValueOnce(null) // Fails for 99999
        .mockResolvedValueOnce({ zone: 4 } as TariffZoneMap);

      tariffZoneMapRepository.find.mockResolvedValue([]); // No pattern matches

      const results = await service.bulkCalculateZones(
        mockTenantId,
        mockCarrierId,
        requests
      );

      expect(results.size).toBe(2); // Should have 2 successful results
      expect(results.get('DE-42349-Fri Mar 01 2024')).toBe(1);
      expect(results.get('DE-78234-Fri Mar 01 2024')).toBe(4);
    });
  });

  describe('getAvailableZones', () => {
    it('should return sorted unique zones for carrier and country', async () => {
      const mockMappings = [
        { zone: 3 },
        { zone: 1 },
        { zone: 4 },
        { zone: 1 }, // Duplicate
        { zone: 2 },
      ] as TariffZoneMap[];

      tariffZoneMapRepository.find.mockResolvedValue(mockMappings);

      const result = await service.getAvailableZones(
        mockTenantId,
        mockCarrierId,
        'DE',
        testDate
      );

      expect(result).toEqual([1, 2, 3, 4]); // Should be sorted and deduplicated
      expect(tariffZoneMapRepository.find).toHaveBeenCalledWith({
        where: {
          tenant_id: mockTenantId,
          carrier_id: mockCarrierId,
          country: 'DE',
          valid_from: expect.any(Object),
          valid_until: expect.any(Object),
        },
        select: ['zone'],
      });
    });

    it('should handle database errors gracefully', async () => {
      tariffZoneMapRepository.find.mockRejectedValue(new Error('Database error'));

      const result = await service.getAvailableZones(
        mockTenantId,
        mockCarrierId,
        'DE',
        testDate
      );

      expect(result).toEqual([]); // Should return empty array on error
    });
  });

  describe('error handling', () => {
    it('should handle database connection errors', async () => {
      tariffZoneMapRepository.findOne.mockRejectedValue(new Error('Connection failed'));

      await expect(
        service.calculateZone(mockTenantId, mockCarrierId, 'DE', '42349', testDate)
      ).rejects.toThrow('Zone calculation failed: Connection failed');
    });

    it('should preserve NotFoundException without wrapping', async () => {
      tariffZoneMapRepository.findOne.mockResolvedValue(null);
      tariffZoneMapRepository.find.mockResolvedValue([]);

      await expect(
        service.calculateZone(mockTenantId, mockCarrierId, 'DE', '99999', testDate)
      ).rejects.toThrow(NotFoundException);
    });
  });
});