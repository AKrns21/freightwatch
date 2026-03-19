import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { In, Repository } from 'typeorm';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';

/** Allowed deviation between sum(line_total) and invoice header total (2%) */
const INVOICE_TOTAL_TOLERANCE_PCT = 0.02;

/** German postal code: exactly 5 digits */
const DE_ZIP_PATTERN = /^\d{5}$/;

/** PLZ prefix: 1–5 digits representing a valid German zip prefix */
const PLZ_PREFIX_PATTERN = /^\d{1,5}$/;

export type ValidationAction = 'reject' | 'hold_for_review' | 'warn';
export type ValidationStatus = 'pass' | 'review' | 'fail';

export interface ValidationViolation {
  entity: 'invoice_line' | 'shipment' | 'tariff_rate' | 'tariff_zone_map';
  rule: string;
  action: ValidationAction;
  detail: string;
  index?: number;
}

export interface ExtractionValidationResult {
  status: ValidationStatus;
  violations: ValidationViolation[];
}

export interface InvoiceHeaderInput {
  total_net: number | null;
}

export interface InvoiceLineInput {
  index: number;
  line_total: number | null;
  weight_kg: number | null;
  dest_zip: string | null;
  dest_country?: string | null;
}

export interface ShipmentInput {
  index: number;
  reference_number: string | null;
}

export interface TariffRateInput {
  index: number;
  weight_from_kg: number;
  weight_to_kg: number;
}

export interface TariffZoneMapInput {
  index: number;
  plz_prefix: string;
}

function deriveStatus(violations: ValidationViolation[]): ValidationStatus {
  if (violations.some((v) => v.action === 'reject')) return 'fail';
  if (violations.some((v) => v.action === 'hold_for_review')) return 'review';
  return 'pass';
}

/**
 * ExtractionValidatorService
 *
 * Shared post-parse, pre-import validator that applies deterministic rules across
 * all parser outputs (invoice, CSV shipment list, tariff rate table, zone map).
 *
 * Rule table:
 *  invoice_line  | sum(line_total) ≈ header.total_net ±2%       | hold_for_review
 *  invoice_line  | weight_kg > 0                                 | reject
 *  invoice_line  | dest_zip matches /^\d{5}$/ (DE only)          | warn
 *  shipment      | reference_number not already in DB (dedup)    | reject
 *  tariff_rate   | weight_from_kg < weight_to_kg                 | reject
 *  tariff_zone_map | plz_prefix within 00000–99999               | reject
 */
@Injectable()
export class ExtractionValidatorService {
  constructor(
    @InjectRepository(Shipment)
    private readonly shipmentRepository: Repository<Shipment>,
  ) {}

  /**
   * Validate invoice lines against the header total and per-line business rules.
   *
   * Rules applied:
   *  1. sum(line_total) ≈ header.total_net ±2%  → hold_for_review
   *  2. weight_kg > 0                            → reject (that line)
   *  3. dest_zip format (DE)                     → warn
   */
  validateInvoice(
    header: InvoiceHeaderInput,
    lines: InvoiceLineInput[],
  ): ExtractionValidationResult {
    const violations: ValidationViolation[] = [];

    // Rule 1 — total reconciliation ±2%
    if (header.total_net != null && lines.length > 0) {
      const lineSum = lines.reduce((acc, l) => acc + (l.line_total ?? 0), 0);
      const tolerance = Math.abs(header.total_net) * INVOICE_TOTAL_TOLERANCE_PCT;
      const diff = Math.abs(header.total_net - lineSum);

      if (diff > tolerance) {
        violations.push({
          entity: 'invoice_line',
          rule: 'invoice_total_reconciliation',
          action: 'hold_for_review',
          detail:
            `Header total ${header.total_net.toFixed(2)} differs from ` +
            `sum of lines ${lineSum.toFixed(2)} by ${diff.toFixed(2)} ` +
            `(tolerance ${tolerance.toFixed(2)}, ${INVOICE_TOTAL_TOLERANCE_PCT * 100}%)`,
        });
      }
    }

    for (const line of lines) {
      // Rule 2 — weight_kg must be positive
      const weight = line.weight_kg;
      if (weight != null && weight <= 0) {
        violations.push({
          entity: 'invoice_line',
          rule: 'weight_positive',
          action: 'reject',
          detail: `Line ${line.index}: weight_kg is ${weight} (must be > 0)`,
          index: line.index,
        });
      }

      // Rule 3 — dest_zip format for DE shipments
      const isDE = !line.dest_country || line.dest_country.toUpperCase() === 'DE';
      if (isDE && line.dest_zip != null && !DE_ZIP_PATTERN.test(line.dest_zip)) {
        violations.push({
          entity: 'invoice_line',
          rule: 'dest_zip_format_de',
          action: 'warn',
          detail: `Line ${line.index}: dest_zip "${line.dest_zip}" does not match /^\\d{5}$/`,
          index: line.index,
        });
      }
    }

    return { status: deriveStatus(violations), violations };
  }

  /**
   * Validate parsed shipments before DB import.
   *
   * Rule 4 — shipment_reference deduplication:
   *   If reference_number already exists for this tenant, reject the row.
   */
  async validateShipments(
    shipments: ShipmentInput[],
    tenantId: string,
  ): Promise<ExtractionValidationResult> {
    const violations: ValidationViolation[] = [];

    const refs = shipments
      .map((s) => s.reference_number)
      .filter((r): r is string => r != null && r.trim() !== '');

    if (refs.length === 0) {
      return { status: 'pass', violations: [] };
    }

    const existing = await this.shipmentRepository.find({
      where: { tenant_id: tenantId, reference_number: In(refs) },
      select: ['reference_number'],
    });

    const existingRefs = new Set(existing.map((s) => s.reference_number));

    for (const shipment of shipments) {
      if (shipment.reference_number && existingRefs.has(shipment.reference_number)) {
        violations.push({
          entity: 'shipment',
          rule: 'reference_number_dedup',
          action: 'reject',
          detail: `Shipment ${shipment.index}: reference_number "${shipment.reference_number}" already exists for tenant`,
          index: shipment.index,
        });
      }
    }

    return { status: deriveStatus(violations), violations };
  }

  /**
   * Validate tariff rates.
   *
   * Rule 5 — weight band integrity: weight_from_kg must be < weight_to_kg.
   *   Rejects the entire table entry when violated.
   */
  validateTariffRates(rates: TariffRateInput[]): ExtractionValidationResult {
    const violations: ValidationViolation[] = [];

    for (const rate of rates) {
      if (rate.weight_from_kg >= rate.weight_to_kg) {
        violations.push({
          entity: 'tariff_rate',
          rule: 'weight_band_integrity',
          action: 'reject',
          detail:
            `Rate ${rate.index}: weight_from_kg (${rate.weight_from_kg}) ` +
            `must be < weight_to_kg (${rate.weight_to_kg})`,
          index: rate.index,
        });
      }
    }

    return { status: deriveStatus(violations), violations };
  }

  /**
   * Validate tariff zone map entries.
   *
   * Rule 6 — PLZ prefix must be 1–5 digits (00000–99999 range).
   *   Rejects entries with non-numeric or out-of-range prefixes.
   */
  validateTariffZoneMap(entries: TariffZoneMapInput[]): ExtractionValidationResult {
    const violations: ValidationViolation[] = [];

    for (const entry of entries) {
      if (!PLZ_PREFIX_PATTERN.test(entry.plz_prefix)) {
        violations.push({
          entity: 'tariff_zone_map',
          rule: 'plz_prefix_valid',
          action: 'reject',
          detail:
            `Entry ${entry.index}: plz_prefix "${entry.plz_prefix}" is not a valid ` +
            `German postal code prefix (expected 1–5 digits, 00000–99999)`,
          index: entry.index,
        });
      }
    }

    return { status: deriveStatus(violations), violations };
  }
}
