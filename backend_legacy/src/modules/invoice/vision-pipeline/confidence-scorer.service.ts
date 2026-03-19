import { Injectable } from '@nestjs/common';
import {
  ExtractedHeader,
  ExtractedLine,
  FieldSource,
  ConfidenceScore,
} from './pipeline.types';

/** Header fields that must be present and direct_ocr for high confidence */
const REQUIRED_HEADER_FIELDS: Array<keyof ExtractedHeader> = [
  'invoice_number',
  'invoice_date',
  'carrier_name',
  'total_net_amount',
];

/** Line fields that must be present per line for high confidence */
const REQUIRED_LINE_FIELDS: Array<keyof ExtractedLine> = [
  'weight_kg',
  'line_total',
  'dest_zip',
];

/**
 * Stage 5 — Per-field confidence scoring
 *
 * Aggregates field-level source annotations from Stage 3 into a single
 * document-level confidence score used by the Review Gate (Stage 6).
 *
 * Formula:
 *   direct_ocr_ratio  = fields tagged "direct_ocr" / total fields examined
 *   completeness_ratio = required fields with a non-null value / total required fields
 *   overall = 0.6 × direct_ocr_ratio + 0.4 × completeness_ratio
 *
 * The 60/40 split reflects that OCR quality matters more than field completeness
 * for the auto-import decision (a mostly-direct_ocr result is more trustworthy
 * than one where many values are LLM-inferred even if all fields are filled).
 */
@Injectable()
export class ConfidenceScorerService {
  score(header: ExtractedHeader, lines: ExtractedLine[]): ConfidenceScore {
    const fieldBreakdown: Record<string, FieldSource> = {};
    let totalFields = 0;
    let directOcrCount = 0;
    let requiredPresent = 0;
    let requiredTotal = 0;

    // ── Header fields ─────────────────────────────────────────────────────

    for (const key of Object.keys(header) as Array<keyof ExtractedHeader>) {
      const field = header[key];
      const src = field.src;
      const isRequired = (REQUIRED_HEADER_FIELDS as string[]).includes(key);

      fieldBreakdown[`header.${key}`] = src;
      totalFields++;
      if (src === 'direct_ocr') directOcrCount++;

      if (isRequired) {
        requiredTotal++;
        if (field.value != null) requiredPresent++;
      }
    }

    // ── Line fields ───────────────────────────────────────────────────────

    lines.forEach((line, lineIdx) => {
      for (const key of Object.keys(line) as Array<keyof ExtractedLine>) {
        const field = line[key];
        const src = field.src;
        const isRequired = (REQUIRED_LINE_FIELDS as string[]).includes(key);

        fieldBreakdown[`lines[${lineIdx}].${key}`] = src;
        totalFields++;
        if (src === 'direct_ocr') directOcrCount++;

        if (isRequired) {
          requiredTotal++;
          if (field.value != null) requiredPresent++;
        }
      }
    });

    const directOcrRatio = totalFields > 0 ? directOcrCount / totalFields : 0;
    const completenessRatio = requiredTotal > 0 ? requiredPresent / requiredTotal : 0;
    const overall = Math.round((0.6 * directOcrRatio + 0.4 * completenessRatio) * 100) / 100;

    return {
      overall,
      direct_ocr_ratio: Math.round(directOcrRatio * 100) / 100,
      completeness_ratio: Math.round(completenessRatio * 100) / 100,
      field_breakdown: fieldBreakdown,
    };
  }
}
