import { Injectable } from '@nestjs/common';
import { ExtractedHeader, ExtractedLine, ValidationResult } from './pipeline.types';

/** Maximum allowable difference between header total and sum of line totals (€) */
const TOTAL_TOLERANCE_EUR = 0.02;

/** Weight sanity range (kg) */
const MIN_WEIGHT_KG = 0.01;
const MAX_WEIGHT_KG = 50_000;

/** Date plausibility window: invoices must fall within this many months in the past */
const MAX_INVOICE_AGE_MONTHS = 36;

/**
 * Stage 4 — Cross-document validation
 *
 * Applies deterministic checks across the merged extraction result:
 *  1. Total reconciliation  — sum(line_totals) ≈ header.total_net_amount ±0.02 EUR
 *  2. Required-field check  — invoice_number, invoice_date, at least one line
 *  3. Date plausibility     — invoice_date not in the future, not older than 3 years
 *  4. Weight sanity         — each weight_kg within realistic range
 */
@Injectable()
export class CrossDocumentValidatorService {
  validate(header: ExtractedHeader, lines: ExtractedLine[]): ValidationResult {
    const errors: string[] = [];
    const warnings: string[] = [];

    // 1. Required fields
    if (!header.invoice_number.value) {
      errors.push('Missing invoice_number');
    }
    if (!header.invoice_date.value) {
      errors.push('Missing invoice_date');
    }
    if (lines.length === 0) {
      errors.push('No line items extracted');
    }

    // 2. Date plausibility
    if (header.invoice_date.value) {
      const invoiceDate = new Date(header.invoice_date.value);
      const now = new Date();

      if (isNaN(invoiceDate.getTime())) {
        errors.push(`Unparseable invoice_date: "${header.invoice_date.value}"`);
      } else {
        if (invoiceDate > now) {
          errors.push(
            `Invoice date ${header.invoice_date.value} is in the future`
          );
        }

        const cutoff = new Date(now);
        cutoff.setMonth(cutoff.getMonth() - MAX_INVOICE_AGE_MONTHS);
        if (invoiceDate < cutoff) {
          warnings.push(
            `Invoice date ${header.invoice_date.value} is older than ${MAX_INVOICE_AGE_MONTHS} months`
          );
        }
      }
    }

    // 3. Total reconciliation
    const headerTotal = header.total_net_amount.value ?? header.total_gross_amount.value;
    if (headerTotal != null && lines.length > 0) {
      const lineSum = lines.reduce((acc, l) => acc + (l.line_total.value ?? 0), 0);
      const diff = Math.abs(headerTotal - lineSum);

      if (diff > TOTAL_TOLERANCE_EUR) {
        warnings.push(
          `Total mismatch: header says ${headerTotal.toFixed(2)} EUR, ` +
          `sum of lines = ${lineSum.toFixed(2)} EUR (diff ${diff.toFixed(2)} EUR)`
        );
      }
    }

    // 4. Weight sanity per line
    for (let i = 0; i < lines.length; i++) {
      const w = lines[i].weight_kg.value;
      if (w != null) {
        if (w < MIN_WEIGHT_KG || w > MAX_WEIGHT_KG) {
          warnings.push(
            `Line ${i + 1}: weight_kg ${w} is outside plausible range ` +
            `(${MIN_WEIGHT_KG}–${MAX_WEIGHT_KG} kg)`
          );
        }
      }
    }

    return {
      valid: errors.length === 0,
      errors,
      warnings,
    };
  }
}
