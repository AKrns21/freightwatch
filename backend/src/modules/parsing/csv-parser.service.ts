import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import * as fs from 'fs/promises';
import * as Papa from 'papaparse';
import { Shipment } from './entities/shipment.entity';
import { ServiceMapperService } from './service-mapper.service';
import { round } from '../../utils/round';

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
        dynamicTyping: true,
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

    shipment.date = this.parseDate(this.extractField(row, [
      'datum', 'date', 'versanddatum', 'shipment_date', 'versand_datum'
    ]));

    if (!shipment.date) {
      this.logger.warn('Skipping row with invalid or missing date:', row);
      return null;
    }

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
    if (weightValue !== null) {
      shipment.weight_kg = this.normalizeWeight(weightValue);
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
      const carrierId = null; // TODO: Extract carrier ID from row data if available
      shipment.service_level = await this.serviceMapperService.normalize(
        tenantId,
        carrierId,
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
      const cleanValue = value.replace(/[^\d.,-]/g, '').replace(',', '.');
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

        if (month >= 1 && month <= 12 && day >= 1 && day <= 31 && year >= 1900 && year <= 2100) {
          const date = new Date(year, month - 1, day);
          if (date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day) {
            return date;
          }
        }
      }
    }

    const isoDate = new Date(strValue);
    if (!isNaN(isoDate.getTime())) {
      return isoDate;
    }

    this.logger.warn(`Unable to parse date: ${strValue}`);
    return null;
  }

}