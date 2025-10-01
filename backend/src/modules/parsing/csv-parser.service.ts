import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import * as fs from 'fs/promises';
import * as Papa from 'papaparse';
import { Shipment } from './entities/shipment.entity';
import { ServiceMapperService } from './service-mapper.service';
import { round } from '../../utils/round';
import { Upload } from '../upload/entities/upload.entity';
import { ParsingTemplate } from './entities/parsing-template.entity';

@Injectable()
export class CsvParserService {
  private readonly logger = new Logger(CsvParserService.name);

  constructor(
    @InjectRepository(Shipment)
    private readonly shipmentRepository: Repository<Shipment>,
    private readonly serviceMapperService: ServiceMapperService,
  ) {}

  async parse(filePath: string, tenantId: string, uploadId: string): Promise<Shipment[]> {
    try {
      const fileContent = await fs.readFile(filePath, 'utf-8');
      
      const parseResult = Papa.parse(fileContent, {
        header: true,
        dynamicTyping: false, // Keep as strings to preserve leading zeros in ZIP codes
        skipEmptyLines: true,
        transformHeader: (header: string) => header.trim().toLowerCase(),
      });

      if (parseResult.errors.length > 0) {
        this.logger.warn(
          `CSV parsing warnings for ${filePath}:`,
          parseResult.errors,
        );
      }

      const shipments: Shipment[] = [];
      
      for (const [index, row] of parseResult.data.entries()) {
        try {
          const shipment = await this.mapRowToShipment(row as any, tenantId, uploadId);
          if (shipment) {
            shipments.push(shipment);
          }
        } catch (error) {
          this.logger.error(
            `Error mapping row ${index + 1} in ${filePath}: ${(error as Error).message}`,
            (error as Error).stack,
          );
        }
      }

      this.logger.log(`Successfully parsed ${shipments.length} shipments from ${filePath}`);
      return shipments;
    } catch (error) {
      this.logger.error(
        `Failed to parse CSV file ${filePath}: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }

  private async mapRowToShipment(row: any, tenantId: string, uploadId: string): Promise<Shipment | null> {
    if (!row || typeof row !== 'object') {
      return null;
    }

    const shipment = this.shipmentRepository.create({
      tenant_id: tenantId,
      upload_id: uploadId,
      extraction_method: 'csv_direct',
      confidence_score: 0.95,
      source_data: row,
    });

    const dateValue = this.extractField(row, [
      'datum', 'date', 'versanddatum', 'shipment_date', 'versand_datum'
    ]);
    const parsedDate = this.parseDate(dateValue);

    if (!parsedDate) {
      this.logger.warn('Skipping row with invalid or missing date:', row);
      return null;
    }

    shipment.date = parsedDate;

    const carrierValue = this.extractField(row, [
      'carrier', 'spediteur', 'frachtführer', 'carrier_name'
    ]);
    if (carrierValue) {
      shipment.source_data.carrier_name = carrierValue;
    }

    shipment.origin_zip = this.extractField(row, [
      'vonplz', 'from_zip', 'origin_zip', 'von_plz', 'absender_plz'
    ]);

    shipment.dest_zip = this.extractField(row, [
      'nachplz', 'to_zip', 'dest_zip', 'nach_plz', 'empfänger_plz'
    ]);

    const weightValue = this.extractField(row, [
      'gewicht', 'weight', 'kg', 'weight_kg', 'gewicht_kg'
    ]);
    const normalized = this.normalizeWeight(weightValue);
    if (normalized !== null) {
      shipment.weight_kg = normalized;
    }

    const costValue = this.extractField(row, [
      'kosten', 'cost', 'betrag', 'total', 'total_amount', 'gesamtbetrag'
    ]);
    if (costValue !== null) {
      shipment.actual_total_amount = round(this.parseNumber(costValue));
    }

    const currencyValue = this.extractField(row, [
      'währung', 'currency', 'ccy', 'waehrung'
    ]);
    if (currencyValue) {
      shipment.currency = currencyValue.toString().toUpperCase().substring(0, 3);
    }

    const referenceValue = this.extractField(row, [
      'referenz', 'reference', 'reference_number', 'sendungsnummer'
    ]);
    if (referenceValue) {
      shipment.reference_number = referenceValue.toString().substring(0, 100);
    }

    const serviceValue = this.extractField(row, [
      'service', 'service_level', 'produkt', 'service_type'
    ]);
    if (serviceValue) {
      shipment.service_level = await this.serviceMapperService.normalize(
        serviceValue.toString(),
      );
    }

    const baseAmountValue = this.extractField(row, [
      'grundpreis', 'base_amount', 'base_cost', 'grundkosten'
    ]);
    if (baseAmountValue !== null) {
      shipment.actual_base_amount = round(this.parseNumber(baseAmountValue));
    }

    const dieselAmountValue = this.extractField(row, [
      'dieselzuschlag', 'diesel_amount', 'diesel_surcharge', 'kraftstoffzuschlag'
    ]);
    if (dieselAmountValue !== null) {
      shipment.diesel_amount = round(this.parseNumber(dieselAmountValue));
    }

    const tollAmountValue = this.extractField(row, [
      'maut', 'toll_amount', 'toll', 'mautgebühren'
    ]);
    if (tollAmountValue !== null) {
      shipment.toll_amount = round(this.parseNumber(tollAmountValue));
    }

    return shipment;
  }

  private extractField(row: any, aliases: string[]): any {
    for (const alias of aliases) {
      const lowerAlias = alias.toLowerCase();
      if (row.hasOwnProperty(lowerAlias) && row[lowerAlias] != null && row[lowerAlias] !== '') {
        return row[lowerAlias];
      }
    }
    return null;
  }

  private normalizeWeight(value: any): number | null {
    if (value == null) return null;
    
    let strValue = value.toString().replace(',', '.');
    const numValue = parseFloat(strValue);
    
    if (isNaN(numValue) || numValue < 0) {
      return null;
    }
    
    return round(numValue);
  }

  private parseNumber(value: any): number {
    if (typeof value === 'number') {
      return value;
    }

    if (typeof value === 'string') {
      // Remove currency symbols and whitespace
      let cleanValue = value.replace(/[^\d.,-]/g, '');

      // Detect format: if both . and , exist, determine which is decimal separator
      const dotPos = cleanValue.lastIndexOf('.');
      const commaPos = cleanValue.lastIndexOf(',');

      if (dotPos > -1 && commaPos > -1) {
        // Both exist - the one that comes last is the decimal separator
        if (dotPos > commaPos) {
          // US format: 1,234.56
          cleanValue = cleanValue.replace(/,/g, '');
        } else {
          // EU format: 1.234,56
          cleanValue = cleanValue.replace(/\./g, '').replace(',', '.');
        }
      } else if (commaPos > -1) {
        // Only comma - could be EU decimal or thousands separator
        // If there are digits after comma, it's decimal: 1234,56
        // If no digits or 3+ digits after comma, it's thousands: 1,234
        const afterComma = cleanValue.substring(commaPos + 1);
        if (afterComma.length > 0 && afterComma.length <= 2) {
          cleanValue = cleanValue.replace(',', '.');
        } else {
          cleanValue = cleanValue.replace(/,/g, '');
        }
      }
      // If only dots, assume US format (dots as thousands separators except last one)
      else if (dotPos > -1) {
        const afterDot = cleanValue.substring(dotPos + 1);
        if (afterDot.length > 2) {
          // Likely thousands separator: 1.234
          cleanValue = cleanValue.replace(/\./g, '');
        }
        // Otherwise keep as is (decimal separator)
      }

      const numValue = parseFloat(cleanValue);
      return isNaN(numValue) ? 0 : numValue;
    }

    return 0;
  }

  private parseDate(value: any): Date | null {
    if (!value) return null;
    
    if (value instanceof Date) {
      return value;
    }
    
    const strValue = value.toString().trim();
    if (!strValue) return null;

    const patterns = [
      /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/, // dd.mm.yyyy
      /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/, // dd/mm/yyyy
      /^(\d{4})-(\d{1,2})-(\d{1,2})$/,  // yyyy-mm-dd
    ];

    for (const pattern of patterns) {
      const match = strValue.match(pattern);
      if (match) {
        let year: number, month: number, day: number;
        
        if (pattern.source.startsWith('^(\\d{4})')) {
          // yyyy-mm-dd format
          year = parseInt(match[1], 10);
          month = parseInt(match[2], 10);
          day = parseInt(match[3], 10);
        } else {
          // dd.mm.yyyy or dd/mm/yyyy format
          day = parseInt(match[1], 10);
          month = parseInt(match[2], 10);
          year = parseInt(match[3], 10);
        }

        // Validate ranges BEFORE creating Date object
        if (month < 1 || month > 12 || day < 1 || day > 31 || year < 1900 || year > 2100) {
          continue; // Skip invalid ranges
        }

        // Create date and verify it matches (catches invalid dates like Feb 31)
        const date = new Date(year, month - 1, day);
        if (date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day) {
          return date;
        }
      }
    }

    // Try ISO format as last resort, but only if no dots (to avoid MM.DD.YYYY misinterpretation)
    if (!strValue.includes('.')) {
      const isoDate = new Date(strValue);
      if (!isNaN(isoDate.getTime())) {
        const year = isoDate.getFullYear();
        if (year >= 1900 && year <= 2100) {
          return isoDate;
        }
      }
    }

    this.logger.warn(`Unable to parse date: ${strValue}`);
    return null;
  }

  /**
   * Parse file using a parsing template (Phase 3 Refactoring)
   */
  async parseWithTemplate(
    upload: Upload,
    template: ParsingTemplate,
  ): Promise<Shipment[]> {
    this.logger.log({
      event: 'parsing_with_template_start',
      upload_id: upload.id,
      template_id: template.id,
      template_name: template.name,
    });

    try {
      // Load file content from storage
      const fileContent = await fs.readFile(upload.storage_url, 'utf-8');

      // Parse CSV with template mappings
      const parseResult = Papa.parse(fileContent, {
        header: true,
        dynamicTyping: false, // Keep as strings to preserve leading zeros in ZIP codes
        skipEmptyLines: true,
        transformHeader: (header: string) => header.trim(),
      });

      if (parseResult.errors.length > 0) {
        this.logger.warn({
          event: 'csv_parsing_warnings',
          upload_id: upload.id,
          errors: parseResult.errors,
        });
      }

      const shipments: Shipment[] = [];
      const mappings = template.mappings as Record<string, any>;

      for (const [index, row] of parseResult.data.entries()) {
        try {
          const shipment = await this.mapRowWithTemplate(
            row as Record<string, any>,
            mappings,
            upload.tenant_id,
            upload.id,
          );

          if (shipment) {
            shipments.push(shipment);
          }
        } catch (error) {
          this.logger.error({
            event: 'row_mapping_error',
            upload_id: upload.id,
            row_index: index + 1,
            error: (error as Error).message,
          });
        }
      }

      this.logger.log({
        event: 'parsing_with_template_complete',
        upload_id: upload.id,
        template_id: template.id,
        shipment_count: shipments.length,
      });

      return shipments;
    } catch (error) {
      this.logger.error({
        event: 'parsing_with_template_error',
        upload_id: upload.id,
        template_id: template.id,
        error: (error as Error).message,
        stack: (error as Error).stack,
      });
      throw error;
    }
  }

  /**
   * Map CSV row to shipment using template mappings
   */
  private async mapRowWithTemplate(
    row: Record<string, any>,
    mappings: Record<string, any>,
    tenantId: string,
    uploadId: string,
  ): Promise<Shipment | null> {
    if (!row || typeof row !== 'object') {
      return null;
    }

    const shipment = this.shipmentRepository.create({
      tenant_id: tenantId,
      upload_id: uploadId,
      extraction_method: 'template',
      confidence_score: 0.95,
      source_data: row,
    });

    // Extract date
    const dateValue = this.extractFieldFromTemplate(row, mappings.date);
    const parsedDate = this.parseDate(dateValue);

    if (!parsedDate) {
      this.logger.warn({
        event: 'invalid_date',
        row,
        date_value: dateValue,
      });
      return null;
    }

    shipment.date = parsedDate;

    // Extract carrier name (store in source_data)
    const carrierValue = this.extractFieldFromTemplate(row, mappings.carrier_name);
    if (carrierValue) {
      shipment.source_data.carrier_name = carrierValue.toString();
    }

    // Extract postal codes
    shipment.origin_zip = this.extractFieldFromTemplate(row, mappings.origin_zip);
    shipment.origin_country = this.extractFieldFromTemplate(row, mappings.origin_country) || 'DE';
    shipment.dest_zip = this.extractFieldFromTemplate(row, mappings.dest_zip);
    shipment.dest_country = this.extractFieldFromTemplate(row, mappings.dest_country) || 'DE';

    // Extract weight
    const weightValue = this.extractFieldFromTemplate(row, mappings.weight_kg);
    if (weightValue !== null) {
      const normalized = this.normalizeWeight(weightValue);
      if (normalized !== null) {
        shipment.weight_kg = normalized;
      }
    }

    // Extract LDM
    const ldmValue = this.extractFieldFromTemplate(row, mappings.ldm);
    if (ldmValue !== null) {
      shipment.length_m = this.parseNumber(ldmValue);
    }

    // Extract pallets
    const palletsValue = this.extractFieldFromTemplate(row, mappings.pallets);
    if (palletsValue !== null) {
      shipment.pallets = parseInt(palletsValue.toString(), 10);
    }

    // Extract currency
    const currencyValue = this.extractFieldFromTemplate(row, mappings.currency);
    if (currencyValue) {
      shipment.currency = currencyValue.toString().toUpperCase().substring(0, 3);
    }

    // Extract amounts
    const totalValue = this.extractFieldFromTemplate(row, mappings.actual_total_amount);
    if (totalValue !== null) {
      shipment.actual_total_amount = round(this.parseNumber(totalValue));
    }

    const baseValue = this.extractFieldFromTemplate(row, mappings.actual_base_amount);
    if (baseValue !== null) {
      shipment.actual_base_amount = round(this.parseNumber(baseValue));
    }

    const dieselValue = this.extractFieldFromTemplate(row, mappings.diesel_amount);
    if (dieselValue !== null) {
      shipment.diesel_amount = round(this.parseNumber(dieselValue));
    }

    const tollValue = this.extractFieldFromTemplate(row, mappings.toll_amount);
    if (tollValue !== null) {
      shipment.toll_amount = round(this.parseNumber(tollValue));
    }

    // Extract reference number
    const referenceValue = this.extractFieldFromTemplate(row, mappings.reference_number);
    if (referenceValue) {
      shipment.reference_number = referenceValue.toString().substring(0, 100);
    }

    // Extract service level
    const serviceValue = this.extractFieldFromTemplate(row, mappings.service_level);
    if (serviceValue) {
      shipment.service_level = await this.serviceMapperService.normalize(
        serviceValue.toString(),
      );
    }

    // Calculate completeness
    const { score, missingFields } = this.calculateCompleteness(shipment);
    shipment.completeness_score = score;
    shipment.missing_fields = missingFields;

    return shipment;
  }

  /**
   * Extract field value from row using template mapping configuration
   */
  private extractFieldFromTemplate(
    row: Record<string, any>,
    mapping: any,
  ): any {
    if (!mapping) return null;

    // Direct column name
    if (typeof mapping === 'string') {
      return row[mapping] ?? null;
    }

    // Mapping with column and keywords
    if (mapping.column) {
      return row[mapping.column] ?? null;
    }

    // Try keywords as fallback
    if (mapping.keywords && Array.isArray(mapping.keywords)) {
      for (const keyword of mapping.keywords) {
        if (row[keyword] != null && row[keyword] !== '') {
          return row[keyword];
        }
      }
    }

    return null;
  }

  /**
   * Calculate completeness score for a shipment (Phase 3 Refactoring)
   */
  private calculateCompleteness(
    shipment: Shipment,
  ): { score: number; missingFields: string[] } {
    const requiredFields = [
      'date',
      'carrier_name',
      'origin_zip',
      'dest_zip',
      'weight_kg',
      'actual_total_amount',
      'currency',
    ];

    const optionalFields = [
      'origin_country',
      'dest_country',
      'service_level',
      'reference_number',
      'actual_base_amount',
      'diesel_amount',
      'toll_amount',
      'length_m',
      'pallets',
    ];

    let presentRequired = 0;
    let presentOptional = 0;
    const missingFields: string[] = [];

    // Check required fields
    for (const field of requiredFields) {
      const value = (shipment as any)[field];
      if (value !== null && value !== undefined && value !== '') {
        presentRequired++;
      } else {
        missingFields.push(field);
      }
    }

    // Check optional fields
    for (const field of optionalFields) {
      const value = (shipment as any)[field];
      if (value !== null && value !== undefined && value !== '') {
        presentOptional++;
      }
    }

    // Calculate score: required fields count 70%, optional fields 30%
    const requiredScore = (presentRequired / requiredFields.length) * 0.7;
    const optionalScore = (presentOptional / optionalFields.length) * 0.3;
    const score = round((requiredScore + optionalScore) * 100);

    return { score, missingFields };
  }

}