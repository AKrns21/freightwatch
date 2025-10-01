import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import * as fs from 'fs/promises';
import { CsvParserService } from './csv-parser.service';
import { Shipment } from './entities/shipment.entity';
import { ServiceMapperService } from './service-mapper.service';

jest.mock('fs/promises');

describe('CsvParserService', () => {
  let service: CsvParserService;
  let shipmentRepository: jest.Mocked<Repository<Shipment>>;
  let serviceMapperService: jest.Mocked<ServiceMapperService>;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        CsvParserService,
        {
          provide: getRepositoryToken(Shipment),
          useValue: {
            create: jest.fn(),
          },
        },
        {
          provide: ServiceMapperService,
          useValue: {
            normalize: jest.fn().mockImplementation(async (serviceText: string) => {
              const normalized = serviceText.toLowerCase().trim();
              if (/express|24h|next.*day|overnight|eilsendung|schnell/i.test(normalized)) {
                return 'EXPRESS';
              }
              if (/same.*day|sameday/i.test(normalized)) {
                return 'SAME_DAY';
              }
              if (/eco|economy|slow|spar|günstig|cheap|sparversand|langsam/i.test(normalized)) {
                return 'ECONOMY';
              }
              if (/premium|priority|first.*class|firstclass/i.test(normalized)) {
                return 'PREMIUM';
              }
              return 'STANDARD';
            }),
          },
        },
      ],
    }).compile();

    service = module.get<CsvParserService>(CsvParserService);
    shipmentRepository = module.get(getRepositoryToken(Shipment));
    serviceMapperService = module.get(ServiceMapperService);
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should be defined', () => {
    expect(service).toBeDefined();
  });

  describe('parse', () => {
    const mockTenantId = 'tenant-123';
    const mockUploadId = 'upload-456';
    const mockFilePath = '/path/to/test.csv';

    beforeEach(() => {
      shipmentRepository.create.mockImplementation((data) => data as Shipment);
    });

    it('should parse CSV with German column names', async () => {
      const csvContent = `Datum,Spediteur,VonPLZ,NachPLZ,Gewicht,Kosten,Währung
01.03.2024,DHL,10115,80331,15.5,45.20,EUR
02.03.2024,UPS,20095,50667,8.3,32.10,EUR`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(2);
      
      const firstShipment = result[0];
      expect(firstShipment.tenant_id).toBe(mockTenantId);
      expect(firstShipment.upload_id).toBe(mockUploadId);
      expect(firstShipment.extraction_method).toBe('csv_direct');
      expect(firstShipment.confidence_score).toBe(0.95);
      expect(firstShipment.date).toEqual(new Date(2024, 2, 1)); // March 1, 2024
      expect(firstShipment.origin_zip).toBe('10115');
      expect(firstShipment.dest_zip).toBe('80331');
      expect(firstShipment.weight_kg).toBe(15.5);
      expect(firstShipment.actual_total_amount).toBe(45.2);
      expect(firstShipment.currency).toBe('EUR');
      expect(firstShipment.source_data.carrier_name).toBe('DHL');
    });

    it('should parse CSV with English column names', async () => {
      const csvContent = `date,carrier,origin_zip,dest_zip,weight,cost,currency
2024-03-01,FedEx,12345,54321,25.75,67.80,USD`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(1);
      
      const shipment = result[0];
      expect(shipment.date).toEqual(new Date(2024, 2, 1)); // March 1, 2024
      expect(shipment.origin_zip).toBe('12345');
      expect(shipment.dest_zip).toBe('54321');
      expect(shipment.weight_kg).toBe(25.75);
      expect(shipment.actual_total_amount).toBe(67.8);
      expect(shipment.currency).toBe('USD');
      expect(shipment.source_data.carrier_name).toBe('FedEx');
    });

    it('should handle weight with comma as decimal separator', async () => {
      const csvContent = `datum,gewicht,kosten
01.03.2024,"12,5","34,90"`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(1);
      expect(result[0].weight_kg).toBe(12.5);
      expect(result[0].actual_total_amount).toBe(34.9);
    });

    it('should parse different date formats', async () => {
      const csvContent = `datum,kosten
01.03.2024,10.00
01/03/2024,20.00
2024-03-01,30.00`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(3);
      
      const expectedDate = new Date(2024, 2, 1); // March 1, 2024
      result.forEach(shipment => {
        expect(shipment.date).toEqual(expectedDate);
      });
    });

    it('should normalize service levels', async () => {
      const csvContent = `datum,service,kosten
01.03.2024,Express,10.00
02.03.2024,24h Delivery,20.00
03.03.2024,Economy Plus,30.00
04.03.2024,Standard,40.00
05.03.2024,Custom Service,50.00`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(5);
      expect(result[0].service_level).toBe('EXPRESS');
      expect(result[1].service_level).toBe('EXPRESS'); // 24h -> EXPRESS
      expect(result[2].service_level).toBe('ECONOMY');
      expect(result[3].service_level).toBe('STANDARD');
      expect(result[4].service_level).toBe('STANDARD'); // Unknown -> STANDARD
    });

    it('should handle cost strings with currency symbols and formatting', async () => {
      const csvContent = `datum,betrag
01.03.2024,"€ 1.234,56"
02.03.2024,"$ 987.65"
03.03.2024,"45,30 EUR"`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(3);
      expect(result[0].actual_total_amount).toBe(1234.56);
      expect(result[1].actual_total_amount).toBe(987.65);
      expect(result[2].actual_total_amount).toBe(45.3);
    });

    it('should skip rows with invalid or missing dates', async () => {
      const csvContent = `datum,kosten
01.03.2024,10.00
invalid-date,20.00
,30.00
02.03.2024,40.00`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(2);
      expect(result[0].actual_total_amount).toBe(10);
      expect(result[1].actual_total_amount).toBe(40);
    });

    it('should handle extended cost breakdown fields', async () => {
      const csvContent = `datum,grundpreis,dieselzuschlag,maut,kosten
01.03.2024,100.00,18.50,5.25,123.75`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(1);
      
      const shipment = result[0];
      expect(shipment.actual_base_amount).toBe(100);
      expect(shipment.diesel_amount).toBe(18.5);
      expect(shipment.toll_amount).toBe(5.25);
      expect(shipment.actual_total_amount).toBe(123.75);
    });

    it('should handle empty and malformed CSV gracefully', async () => {
      const csvContent = `datum,kosten
`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(0);
    });

    it('should preserve original data in source_data field', async () => {
      const csvContent = `custom_field,datum,kosten,extra_info
special_value,01.03.2024,10.00,additional_data`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);

      const result = await service.parse(mockFilePath, mockTenantId, mockUploadId);

      expect(result).toHaveLength(1);
      
      const shipment = result[0];
      expect(shipment.source_data).toEqual({
        custom_field: 'special_value',
        datum: '01.03.2024',
        kosten: '10.00', // String because dynamicTyping is false
        extra_info: 'additional_data',
      });
    });

    it('should handle file read errors', async () => {
      (fs.readFile as jest.Mock).mockRejectedValue(new Error('File not found'));

      await expect(
        service.parse(mockFilePath, mockTenantId, mockUploadId)
      ).rejects.toThrow('File not found');
    });
  });

  describe('date parsing edge cases', () => {
    it('should validate date components', async () => {
      const csvContent = `datum,kosten
32.01.2024,10.00
01.13.2024,20.00
01.01.1800,30.00
01.01.2200,40.00`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);
      shipmentRepository.create.mockImplementation((data) => data as Shipment);

      const result = await service.parse('/test.csv', 'tenant', 'upload');

      expect(result).toHaveLength(0); // All dates should be invalid
    });
  });

  describe('weight normalization edge cases', () => {
    it('should handle negative and invalid weights', async () => {
      const csvContent = `datum,gewicht,kosten
01.03.2024,-5.0,10.00
02.03.2024,invalid,20.00
03.03.2024,abc,30.00
04.03.2024,15.5,40.00`;

      (fs.readFile as jest.Mock).mockResolvedValue(csvContent);
      shipmentRepository.create.mockImplementation((data) => data as Shipment);

      const result = await service.parse('/test.csv', 'tenant', 'upload');

      expect(result).toHaveLength(4);
      expect(result[0].weight_kg).toBeUndefined(); // negative weight not set
      expect(result[1].weight_kg).toBeUndefined(); // invalid weight not set
      expect(result[2].weight_kg).toBeUndefined(); // invalid weight not set
      expect(result[3].weight_kg).toBe(15.5); // valid weight
    });
  });
});