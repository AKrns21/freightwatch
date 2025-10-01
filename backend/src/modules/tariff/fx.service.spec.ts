import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { NotFoundException } from '@nestjs/common';
import { FxService } from './fx.service';
import { FxRate } from './entities/fx-rate.entity';

describe('FxService', () => {
  let service: FxService;
  let fxRateRepository: jest.Mocked<Repository<FxRate>>;

  const testDate = new Date('2024-03-01');

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        FxService,
        {
          provide: getRepositoryToken(FxRate),
          useValue: {
            findOne: jest.fn(),
            find: jest.fn(),
            create: jest.fn(),
            save: jest.fn(),
            createQueryBuilder: jest.fn(),
          },
        },
      ],
    }).compile();

    service = module.get<FxService>(FxService);
    fxRateRepository = module.get(getRepositoryToken(FxRate));
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should be defined', () => {
    expect(service).toBeDefined();
  });

  describe('getRate', () => {
    it('should return 1.0 for same currency conversion', async () => {
      const result = await service.getRate('EUR', 'EUR', testDate);
      expect(result).toBe(1.0);
      expect(fxRateRepository.findOne).not.toHaveBeenCalled();
    });

    it('should handle case insensitive currency codes', async () => {
      const result = await service.getRate('eur', 'EUR', testDate);
      expect(result).toBe(1.0);
    });

    it('should return direct rate when found', async () => {
      const mockFxRate = {
        rate_date: new Date('2024-02-15'),
        from_ccy: 'EUR',
        to_ccy: 'USD',
        rate: 1.0850,
        source: 'ecb',
      } as FxRate;

      fxRateRepository.findOne.mockResolvedValue(mockFxRate);

      const result = await service.getRate('EUR', 'USD', testDate);

      expect(result).toBe(1.0850);
      expect(fxRateRepository.findOne).toHaveBeenCalledWith({
        where: {
          from_ccy: 'EUR',
          to_ccy: 'USD',
          rate_date: expect.any(Object), // LessThanOrEqual
        },
        order: {
          rate_date: 'DESC',
        },
      });
    });

    it('should try inverse rate when direct rate not found', async () => {
      fxRateRepository.findOne
        .mockResolvedValueOnce(null) // Direct rate not found
        .mockResolvedValueOnce({     // Inverse rate found
          rate_date: new Date('2024-02-15'),
          from_ccy: 'USD',
          to_ccy: 'EUR',
          rate: 0.9220,
          source: 'ecb',
        } as FxRate);

      const result = await service.getRate('EUR', 'USD', testDate);

      expect(result).toBeCloseTo(1.0846, 4); // 1 / 0.9220
      expect(fxRateRepository.findOne).toHaveBeenCalledTimes(2);
      
      // Check inverse lookup was called
      expect(fxRateRepository.findOne).toHaveBeenNthCalledWith(2, {
        where: {
          from_ccy: 'USD', // Swapped
          to_ccy: 'EUR',   // Swapped
          rate_date: expect.any(Object),
        },
        order: {
          rate_date: 'DESC',
        },
      });
    });

    it('should throw NotFoundException when no rate found', async () => {
      fxRateRepository.findOne.mockResolvedValue(null);

      await expect(
        service.getRate('EUR', 'XYZ', testDate)
      ).rejects.toThrow(NotFoundException);

      expect(fxRateRepository.findOne).toHaveBeenCalledTimes(2); // Direct + inverse
    });

    it('should use most recent rate when multiple exist', async () => {
      const mockFxRate = {
        rate_date: new Date('2024-02-28'), // Most recent
        from_ccy: 'EUR',
        to_ccy: 'CHF',
        rate: 0.9875,
        source: 'ecb',
      } as FxRate;

      fxRateRepository.findOne.mockResolvedValue(mockFxRate);

      const result = await service.getRate('EUR', 'CHF', testDate);

      expect(result).toBe(0.9875);
      expect(fxRateRepository.findOne).toHaveBeenCalledWith({
        where: expect.objectContaining({
          from_ccy: 'EUR',
          to_ccy: 'CHF',
        }),
        order: {
          rate_date: 'DESC', // Ensures most recent
        },
      });
    });

    it('should handle database errors gracefully', async () => {
      fxRateRepository.findOne.mockRejectedValue(new Error('Database connection failed'));

      await expect(
        service.getRate('EUR', 'USD', testDate)
      ).rejects.toThrow('FX rate lookup failed: Database connection failed');
    });

    it('should preserve NotFoundException without wrapping', async () => {
      fxRateRepository.findOne.mockResolvedValue(null);

      await expect(
        service.getRate('EUR', 'XYZ', testDate)
      ).rejects.toThrow(NotFoundException);
    });
  });

  describe('bulkGetRates', () => {
    it('should process multiple rate requests with caching', async () => {
      const requests = [
        { fromCcy: 'EUR', toCcy: 'USD', date: testDate },
        { fromCcy: 'EUR', toCcy: 'CHF', date: testDate },
        { fromCcy: 'EUR', toCcy: 'USD', date: testDate }, // Duplicate for caching
      ];

      fxRateRepository.findOne
        .mockResolvedValueOnce({ rate: 1.0850 } as FxRate) // EUR/USD
        .mockResolvedValueOnce({ rate: 0.9875 } as FxRate); // EUR/CHF

      const results = await service.bulkGetRates(requests);

      expect(results.size).toBe(2); // Should deduplicate
      expect(results.get('EUR-USD-Fri Mar 01 2024')).toBe(1.0850);
      expect(results.get('EUR-CHF-Fri Mar 01 2024')).toBe(0.9875);
      
      // Should only call repository twice due to caching
      expect(fxRateRepository.findOne).toHaveBeenCalledTimes(2);
    });

    it('should continue processing even when some rates fail', async () => {
      const requests = [
        { fromCcy: 'EUR', toCcy: 'USD', date: testDate },
        { fromCcy: 'EUR', toCcy: 'XYZ', date: testDate }, // Will fail
        { fromCcy: 'EUR', toCcy: 'CHF', date: testDate },
      ];

      fxRateRepository.findOne
        .mockResolvedValueOnce({ rate: 1.0850 } as FxRate)
        .mockResolvedValueOnce(null) // EUR/XYZ direct fails
        .mockResolvedValueOnce(null) // EUR/XYZ inverse fails
        .mockResolvedValueOnce({ rate: 0.9875 } as FxRate);

      const results = await service.bulkGetRates(requests);

      expect(results.size).toBe(2); // Should have 2 successful results
      expect(results.get('EUR-USD-Fri Mar 01 2024')).toBe(1.0850);
      expect(results.get('EUR-CHF-Fri Mar 01 2024')).toBe(0.9875);
    });
  });

  describe('seedCommonRates', () => {
    it('should seed common FX rates', async () => {
      fxRateRepository.findOne.mockResolvedValue(null); // No existing rates
      fxRateRepository.create.mockImplementation((data) => data as FxRate);
      fxRateRepository.save.mockResolvedValue({} as FxRate);

      await service.seedCommonRates('manual');

      expect(fxRateRepository.create).toHaveBeenCalledTimes(4);
      expect(fxRateRepository.save).toHaveBeenCalledTimes(4);

      // Check specific rates
      expect(fxRateRepository.create).toHaveBeenCalledWith({
        rate_date: new Date('2023-01-01'),
        from_ccy: 'EUR',
        to_ccy: 'CHF',
        rate: 0.9850,
        source: 'manual',
      });

      expect(fxRateRepository.create).toHaveBeenCalledWith({
        rate_date: new Date('2023-01-01'),
        from_ccy: 'EUR',
        to_ccy: 'USD',
        rate: 1.0650,
        source: 'manual',
      });
    });

    it('should skip existing rates when seeding', async () => {
      const existingRate = {
        rate_date: new Date('2023-01-01'),
        from_ccy: 'EUR',
        to_ccy: 'USD',
        rate: 1.0650,
        source: 'existing',
      } as FxRate;

      fxRateRepository.findOne
        .mockResolvedValueOnce(existingRate) // EUR/CHF exists
        .mockResolvedValueOnce(null)         // EUR/USD doesn't exist
        .mockResolvedValueOnce(null)         // EUR/GBP doesn't exist
        .mockResolvedValueOnce(null);        // EUR/PLN doesn't exist

      fxRateRepository.create.mockImplementation((data) => data as FxRate);
      fxRateRepository.save.mockResolvedValue({} as FxRate);

      await service.seedCommonRates('manual');

      expect(fxRateRepository.create).toHaveBeenCalledTimes(3); // Skip existing CHF rate
      expect(fxRateRepository.save).toHaveBeenCalledTimes(3);
    });

    it('should handle seeding errors', async () => {
      fxRateRepository.findOne.mockResolvedValue(null);
      fxRateRepository.create.mockImplementation((data) => data as FxRate);
      fxRateRepository.save.mockRejectedValue(new Error('Save failed'));

      await expect(service.seedCommonRates('manual')).rejects.toThrow('Save failed');
    });
  });

  describe('addRate', () => {
    it('should add a new FX rate successfully', async () => {
      const mockFxRate = {
        rate_date: testDate,
        from_ccy: 'EUR',
        to_ccy: 'JPY',
        rate: 145.25,
        source: 'manual',
      } as FxRate;

      fxRateRepository.create.mockReturnValue(mockFxRate);
      fxRateRepository.save.mockResolvedValue(mockFxRate);

      await service.addRate('EUR', 'JPY', 145.25, testDate, 'manual');

      expect(fxRateRepository.create).toHaveBeenCalledWith({
        rate_date: testDate,
        from_ccy: 'EUR',
        to_ccy: 'JPY',
        rate: 145.25,
        source: 'manual',
      });
      expect(fxRateRepository.save).toHaveBeenCalledWith(mockFxRate);
    });

    it('should normalize currency codes', async () => {
      fxRateRepository.create.mockReturnValue({} as FxRate);
      fxRateRepository.save.mockResolvedValue({} as FxRate);

      await service.addRate('eur', 'usd', 1.0850, testDate, 'api');

      expect(fxRateRepository.create).toHaveBeenCalledWith({
        rate_date: testDate,
        from_ccy: 'EUR',
        to_ccy: 'USD',
        rate: 1.0850,
        source: 'api',
      });
    });

    it('should reject same currency pairs', async () => {
      await expect(
        service.addRate('EUR', 'EUR', 1.0, testDate, 'manual')
      ).rejects.toThrow('Cannot add FX rate for same currency pair');
    });

    it('should reject negative or zero rates', async () => {
      await expect(
        service.addRate('EUR', 'USD', 0, testDate, 'manual')
      ).rejects.toThrow('FX rate must be positive');

      await expect(
        service.addRate('EUR', 'USD', -1.5, testDate, 'manual')
      ).rejects.toThrow('FX rate must be positive');
    });
  });

  describe('getAvailableCurrencies', () => {
    it('should return sorted unique currencies', async () => {
      const mockQueryBuilder = {
        select: jest.fn().mockReturnThis(),
        union: jest.fn().mockReturnThis(),
        where: jest.fn().mockReturnThis(),
        orderBy: jest.fn().mockReturnThis(),
        getRawMany: jest.fn().mockResolvedValue([
          { currency: 'USD' },
          { currency: 'CHF' },
          { currency: 'GBP' },
          { currency: 'EUR' },
        ]),
      };

      fxRateRepository.createQueryBuilder.mockReturnValue(mockQueryBuilder);

      const result = await service.getAvailableCurrencies();

      expect(result).toEqual(['CHF', 'EUR', 'GBP', 'USD']);
    });

    it('should add EUR if not present in results', async () => {
      const mockQueryBuilder = {
        select: jest.fn().mockReturnThis(),
        union: jest.fn().mockReturnThis(),
        where: jest.fn().mockReturnThis(),
        orderBy: jest.fn().mockReturnThis(),
        getRawMany: jest.fn().mockResolvedValue([
          { currency: 'USD' },
          { currency: 'CHF' },
        ]),
      };

      fxRateRepository.createQueryBuilder.mockReturnValue(mockQueryBuilder);

      const result = await service.getAvailableCurrencies();

      expect(result).toEqual(['CHF', 'EUR', 'USD']); // EUR added automatically
    });

    it('should handle database errors gracefully', async () => {
      fxRateRepository.createQueryBuilder.mockImplementation(() => {
        throw new Error('Query failed');
      });

      const result = await service.getAvailableCurrencies();

      expect(result).toEqual(['EUR']); // Fallback to EUR only
    });
  });
});