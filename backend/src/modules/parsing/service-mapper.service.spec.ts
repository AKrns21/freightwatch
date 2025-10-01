import { Test, TestingModule } from '@nestjs/testing';
import { ServiceMapperService } from './service-mapper.service';

/**
 * ServiceMapperService Tests - Simplified version (Phase 2 Refactoring)
 *
 * NO DATABASE MOCKING - pure fuzzy matching tests
 */
describe('ServiceMapperService', () => {
  let service: ServiceMapperService;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [ServiceMapperService],
    }).compile();

    service = module.get<ServiceMapperService>(ServiceMapperService);
  });

  it('should be defined', () => {
    expect(service).toBeDefined();
  });

  describe('normalize', () => {
    it('should return STANDARD for empty service text', async () => {
      expect(await service.normalize('')).toBe('STANDARD');
      expect(await service.normalize(null as any)).toBe('STANDARD');
      expect(await service.normalize(undefined as any)).toBe('STANDARD');
    });

    it('should match express variants', async () => {
      expect(await service.normalize('Express Delivery')).toBe('EXPRESS');
      expect(await service.normalize('24h Service')).toBe('EXPRESS');
      expect(await service.normalize('Overnight')).toBe('EXPRESS');
      expect(await service.normalize('Next Day Delivery')).toBe('EXPRESS');
      expect(await service.normalize('Eilsendung')).toBe('EXPRESS');
      expect(await service.normalize('Schnell Service')).toBe('EXPRESS');
    });

    it('should match same day variants', async () => {
      expect(await service.normalize('Same Day')).toBe('SAME_DAY');
      expect(await service.normalize('SameDay Delivery')).toBe('SAME_DAY');
      expect(await service.normalize('Same Day Service')).toBe('SAME_DAY');
    });

    it('should match economy variants', async () => {
      expect(await service.normalize('Economy')).toBe('ECONOMY');
      expect(await service.normalize('Eco Delivery')).toBe('ECONOMY');
      expect(await service.normalize('Slow Service')).toBe('ECONOMY');
      expect(await service.normalize('Spar Versand')).toBe('ECONOMY');
      expect(await service.normalize('Günstig')).toBe('ECONOMY');
      expect(await service.normalize('Cheap Delivery')).toBe('ECONOMY');
      expect(await service.normalize('Sparversand')).toBe('ECONOMY');
      expect(await service.normalize('Langsam')).toBe('ECONOMY');
    });

    it('should match premium variants', async () => {
      expect(await service.normalize('Premium Service')).toBe('PREMIUM');
      expect(await service.normalize('Priority Delivery')).toBe('PREMIUM');
      expect(await service.normalize('First Class')).toBe('PREMIUM');
      expect(await service.normalize('FirstClass Service')).toBe('PREMIUM');
    });

    it('should match standard variants and default to STANDARD', async () => {
      expect(await service.normalize('Standard')).toBe('STANDARD');
      expect(await service.normalize('Normal Delivery')).toBe('STANDARD');
      expect(await service.normalize('Regular Service')).toBe('STANDARD');
      expect(await service.normalize('Unknown Service Type')).toBe('STANDARD');
    });

    it('should be case-insensitive', async () => {
      expect(await service.normalize('EXPRESS')).toBe('EXPRESS');
      expect(await service.normalize('express')).toBe('EXPRESS');
      expect(await service.normalize('ExPrEsS')).toBe('EXPRESS');
    });

    it('should handle whitespace', async () => {
      expect(await service.normalize('  Express  ')).toBe('EXPRESS');
      expect(await service.normalize('\tEconomy\n')).toBe('ECONOMY');
    });
  });

  describe('bulkNormalize', () => {
    it('should normalize multiple service texts efficiently', async () => {
      const serviceTexts = ['Express', 'Standard', 'Express', 'Economy'];
      const result = await service.bulkNormalize(serviceTexts);

      expect(result.size).toBe(3); // Should deduplicate
      expect(result.get('Express')).toBe('EXPRESS');
      expect(result.get('Standard')).toBe('STANDARD');
      expect(result.get('Economy')).toBe('ECONOMY');
    });

    it('should handle empty array', async () => {
      const result = await service.bulkNormalize([]);
      expect(result.size).toBe(0);
    });

    it('should deduplicate identical inputs', async () => {
      const serviceTexts = ['Express', 'express', 'EXPRESS'];
      const result = await service.bulkNormalize(serviceTexts);

      // Each variation is stored separately in the map
      expect(result.size).toBe(3);
      expect(result.get('Express')).toBe('EXPRESS');
      expect(result.get('express')).toBe('EXPRESS');
      expect(result.get('EXPRESS')).toBe('EXPRESS');
    });
  });
});
