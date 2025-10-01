import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { ServiceMapperService } from './service-mapper.service';
import { ServiceAlias } from './entities/service-alias.entity';

describe('ServiceMapperService', () => {
  let service: ServiceMapperService;
  let serviceAliasRepository: jest.Mocked<Repository<ServiceAlias>>;

  const mockTenantId = 'tenant-123';
  const mockCarrierId = 'carrier-456';

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        ServiceMapperService,
        {
          provide: getRepositoryToken(ServiceAlias),
          useValue: {
            findOne: jest.fn(),
            create: jest.fn(),
            save: jest.fn(),
          },
        },
      ],
    }).compile();

    service = module.get<ServiceMapperService>(ServiceMapperService);
    serviceAliasRepository = module.get(getRepositoryToken(ServiceAlias));
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should be defined', () => {
    expect(service).toBeDefined();
  });

  describe('normalize', () => {
    it('should return STANDARD for empty service text', async () => {
      const result = await service.normalize(mockTenantId, mockCarrierId, '');
      expect(result).toBe('STANDARD');
    });

    it('should return STANDARD for null service text', async () => {
      const result = await service.normalize(mockTenantId, mockCarrierId, null as any);
      expect(result).toBe('STANDARD');
    });

    it('should lookup tenant-specific aliases first', async () => {
      serviceAliasRepository.findOne.mockResolvedValueOnce({
        tenant_id: mockTenantId,
        carrier_id: null,
        alias_text: 'custom express',
        service_code: 'EXPRESS',
      } as ServiceAlias);

      const result = await service.normalize(mockTenantId, mockCarrierId, 'Custom Express');

      expect(serviceAliasRepository.findOne).toHaveBeenCalledWith({
        where: {
          tenant_id: mockTenantId,
          alias_text: 'custom express',
        },
      });
      expect(result).toBe('EXPRESS');
    });

    it('should fallback to carrier-specific aliases when tenant lookup fails', async () => {
      serviceAliasRepository.findOne
        .mockResolvedValueOnce(null) // tenant-specific lookup fails
        .mockResolvedValueOnce({     // carrier-specific lookup succeeds
          tenant_id: null,
          carrier_id: mockCarrierId,
          alias_text: 'ups express',
          service_code: 'EXPRESS',
        } as ServiceAlias);

      const result = await service.normalize(mockTenantId, mockCarrierId, 'UPS Express');

      expect(serviceAliasRepository.findOne).toHaveBeenCalledTimes(2);
      expect(serviceAliasRepository.findOne).toHaveBeenNthCalledWith(2, {
        where: {
          tenant_id: null,
          carrier_id: mockCarrierId,
          alias_text: 'ups express',
        },
      });
      expect(result).toBe('EXPRESS');
    });

    it('should fallback to global aliases when specific lookups fail', async () => {
      serviceAliasRepository.findOne
        .mockResolvedValueOnce(null) // tenant-specific fails
        .mockResolvedValueOnce(null) // carrier-specific fails
        .mockResolvedValueOnce({     // global lookup succeeds
          tenant_id: null,
          carrier_id: null,
          alias_text: '24h',
          service_code: 'EXPRESS',
        } as ServiceAlias);

      const result = await service.normalize(mockTenantId, mockCarrierId, '24h');

      expect(serviceAliasRepository.findOne).toHaveBeenCalledTimes(3);
      expect(serviceAliasRepository.findOne).toHaveBeenNthCalledWith(3, {
        where: {
          tenant_id: null,
          carrier_id: null,
          alias_text: '24h',
        },
      });
      expect(result).toBe('EXPRESS');
    });

    it('should skip carrier lookup when carrierId is null', async () => {
      serviceAliasRepository.findOne
        .mockResolvedValueOnce(null) // tenant-specific fails
        .mockResolvedValueOnce({     // global lookup succeeds
          tenant_id: null,
          carrier_id: null,
          alias_text: 'standard',
          service_code: 'STANDARD',
        } as ServiceAlias);

      const result = await service.normalize(mockTenantId, null, 'standard');

      expect(serviceAliasRepository.findOne).toHaveBeenCalledTimes(2);
      expect(result).toBe('STANDARD');
    });

    describe('fuzzy matching fallback', () => {
      beforeEach(() => {
        serviceAliasRepository.findOne.mockResolvedValue(null);
      });

      it('should match express variants', async () => {
        const testCases = [
          'Express Delivery',
          '24h Service',
          'Overnight',
          'Next Day Delivery',
          'Eilsendung',
          'Schnell Service',
        ];

        for (const testCase of testCases) {
          const result = await service.normalize(mockTenantId, mockCarrierId, testCase);
          expect(result).toBe('EXPRESS');
        }
      });

      it('should match same day variants', async () => {
        const testCases = [
          'Same Day',
          'SameDay Delivery',
          'Same Day Service',
        ];

        for (const testCase of testCases) {
          const result = await service.normalize(mockTenantId, mockCarrierId, testCase);
          expect(result).toBe('SAME_DAY');
        }
      });

      it('should match economy variants', async () => {
        const testCases = [
          'Economy',
          'Eco Delivery',
          'Slow Service',
          'Spar Versand',
          'Günstig',
          'Cheap Delivery',
          'Sparversand',
          'Langsam',
        ];

        for (const testCase of testCases) {
          const result = await service.normalize(mockTenantId, mockCarrierId, testCase);
          expect(result).toBe('ECONOMY');
        }
      });

      it('should match premium variants', async () => {
        const testCases = [
          'Premium Service',
          'Priority Delivery',
          'First Class',
          'FirstClass Service',
        ];

        for (const testCase of testCases) {
          const result = await service.normalize(mockTenantId, mockCarrierId, testCase);
          expect(result).toBe('PREMIUM');
        }
      });

      it('should match standard variants', async () => {
        const testCases = [
          'Standard',
          'Normal Delivery',
          'Regular Service',
          'Default',
          'Standardversand',
          'Normalversand',
        ];

        for (const testCase of testCases) {
          const result = await service.normalize(mockTenantId, mockCarrierId, testCase);
          expect(result).toBe('STANDARD');
        }
      });

      it('should default to STANDARD for unmatched text', async () => {
        const result = await service.normalize(mockTenantId, mockCarrierId, 'Unknown Service Type');
        expect(result).toBe('STANDARD');
      });
    });

    it('should handle database errors gracefully', async () => {
      serviceAliasRepository.findOne.mockRejectedValue(new Error('Database error'));

      const result = await service.normalize(mockTenantId, mockCarrierId, 'Express');
      
      expect(result).toBe('EXPRESS'); // Should fallback to fuzzy matching
    });
  });

  describe('bulkNormalize', () => {
    it('should normalize multiple service texts efficiently', async () => {
      serviceAliasRepository.findOne.mockResolvedValue(null);

      const serviceTexts = ['Express', 'Standard', 'Express', 'Economy'];
      const result = await service.bulkNormalize(mockTenantId, mockCarrierId, serviceTexts);

      expect(result.size).toBe(3); // Should deduplicate
      expect(result.get('Express')).toBe('EXPRESS');
      expect(result.get('Standard')).toBe('STANDARD');
      expect(result.get('Economy')).toBe('ECONOMY');
    });
  });

  describe('addTenantAlias', () => {
    it('should create and save tenant-specific alias', async () => {
      const mockAlias = {
        tenant_id: mockTenantId,
        carrier_id: null,
        alias_text: 'custom service',
        service_code: 'EXPRESS',
      };

      serviceAliasRepository.create.mockReturnValue(mockAlias as ServiceAlias);
      serviceAliasRepository.save.mockResolvedValue(mockAlias as ServiceAlias);

      await service.addTenantAlias(mockTenantId, 'Custom Service', 'EXPRESS');

      expect(serviceAliasRepository.create).toHaveBeenCalledWith({
        tenant_id: mockTenantId,
        carrier_id: null,
        alias_text: 'custom service',
        service_code: 'EXPRESS',
      });
      expect(serviceAliasRepository.save).toHaveBeenCalledWith(mockAlias);
    });

    it('should handle save errors', async () => {
      serviceAliasRepository.create.mockReturnValue({} as ServiceAlias);
      serviceAliasRepository.save.mockRejectedValue(new Error('Save failed'));

      await expect(
        service.addTenantAlias(mockTenantId, 'Custom Service', 'EXPRESS')
      ).rejects.toThrow('Save failed');
    });
  });

  describe('addCarrierAlias', () => {
    it('should create and save carrier-specific alias', async () => {
      const mockAlias = {
        tenant_id: null,
        carrier_id: mockCarrierId,
        alias_text: 'carrier express',
        service_code: 'EXPRESS',
      };

      serviceAliasRepository.create.mockReturnValue(mockAlias as ServiceAlias);
      serviceAliasRepository.save.mockResolvedValue(mockAlias as ServiceAlias);

      await service.addCarrierAlias(mockCarrierId, 'Carrier Express', 'EXPRESS');

      expect(serviceAliasRepository.create).toHaveBeenCalledWith({
        tenant_id: null,
        carrier_id: mockCarrierId,
        alias_text: 'carrier express',
        service_code: 'EXPRESS',
      });
      expect(serviceAliasRepository.save).toHaveBeenCalledWith(mockAlias);
    });
  });
});