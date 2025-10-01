import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { InvoiceLine } from './entities/invoice-line.entity';
import { Shipment } from '../parsing/entities/shipment.entity';

/**
 * Match result for invoice line to shipment
 */
export interface MatchResult {
  invoice_line_id: string;
  shipment_id: string | null;
  confidence: number;
  match_type: 'exact' | 'fuzzy' | 'manual' | 'unmatched';
  match_criteria: string[];
  issues?: string[];
}

/**
 * Matching statistics
 */
export interface MatchingStats {
  total_lines: number;
  matched: number;
  unmatched: number;
  ambiguous: number;
  manual: number;
  avg_confidence: number;
}

/**
 * InvoiceMatcherService - Match invoice lines to shipments
 *
 * Matches invoice line items to existing shipments using multiple criteria:
 * - Reference number matching
 * - Date + Route + Weight matching
 * - Fuzzy matching with confidence scoring
 * - Manual matching support
 *
 * Strategy:
 * 1. Try exact reference number match (100% confidence)
 * 2. Try multi-criteria exact match (95% confidence)
 * 3. Try fuzzy matching (70-90% confidence)
 * 4. Mark as unmatched if confidence < 70%
 */
@Injectable()
export class InvoiceMatcherService {
  private readonly logger = new Logger(InvoiceMatcherService.name);

  constructor(
    @InjectRepository(InvoiceLine)
    private readonly lineRepo: Repository<InvoiceLine>,
    @InjectRepository(Shipment)
    private readonly shipmentRepo: Repository<Shipment>,
  ) {}

  /**
   * Match all lines in an invoice to shipments
   */
  async matchInvoiceLines(
    invoiceId: string,
    tenantId: string,
    projectId?: string,
  ): Promise<MatchingStats> {
    this.logger.log({
      event: 'match_invoice_lines_start',
      invoice_id: invoiceId,
      project_id: projectId,
    });

    // Load invoice lines
    const lines = await this.lineRepo.find({
      where: { invoice_id: invoiceId, tenant_id: tenantId },
    });

    let matched = 0;
    let unmatched = 0;
    let ambiguous = 0;
    let manual = 0;
    let confidenceSum = 0;

    for (const line of lines) {
      // Skip already matched lines
      if (line.match_status === 'matched' && line.shipment_id) {
        matched++;
        confidenceSum += line.match_confidence || 1.0;
        continue;
      }

      // Try to match
      const result = await this.matchLine(line, tenantId, projectId);

      // Update line with match result
      await this.lineRepo.update(line.id, {
        shipment_id: result.shipment_id,
        match_status: result.match_type,
        match_confidence: result.confidence,
        meta: {
          ...line.meta,
          match_criteria: result.match_criteria,
          match_issues: result.issues,
        },
      });

      // Update statistics
      if (result.match_type === 'unmatched') {
        unmatched++;
      } else if (result.match_type === 'manual') {
        manual++;
        confidenceSum += result.confidence;
      } else {
        matched++;
        confidenceSum += result.confidence;
      }
    }

    const stats: MatchingStats = {
      total_lines: lines.length,
      matched,
      unmatched,
      ambiguous,
      manual,
      avg_confidence:
        lines.length > 0 ? confidenceSum / lines.length : 0,
    };

    this.logger.log({
      event: 'match_invoice_lines_complete',
      invoice_id: invoiceId,
      stats,
    });

    return stats;
  }

  /**
   * Match a single invoice line to a shipment
   */
  async matchLine(
    line: InvoiceLine,
    tenantId: string,
    projectId?: string,
  ): Promise<MatchResult> {
    // Build base query
    const query = this.shipmentRepo
      .createQueryBuilder('shipment')
      .where('shipment.tenant_id = :tenantId', { tenantId });

    // Scope to project if provided
    if (projectId) {
      query.andWhere('shipment.project_id = :projectId', {
        projectId,
      });
    }

    // Strategy 1: Exact reference match
    if (line.shipment_reference) {
      const exactMatch = await query
        .clone()
        .andWhere(
          '(shipment.reference_number = :ref OR shipment.source_data::text LIKE :refLike)',
          {
            ref: line.shipment_reference,
            refLike: `%${line.shipment_reference}%`,
          },
        )
        .getOne();

      if (exactMatch) {
        return {
          invoice_line_id: line.id,
          shipment_id: exactMatch.id,
          confidence: 1.0,
          match_type: 'exact',
          match_criteria: ['reference_number'],
        };
      }
    }

    // Strategy 2: Multi-criteria exact match
    const candidates = await this.findCandidateShipments(
      line,
      query,
    );

    if (candidates.length === 0) {
      return {
        invoice_line_id: line.id,
        shipment_id: null,
        confidence: 0,
        match_type: 'unmatched',
        match_criteria: [],
        issues: ['No matching shipments found'],
      };
    }

    // Strategy 3: Score candidates
    const scored = candidates.map((shipment) =>
      this.scoreMatch(line, shipment),
    );

    // Sort by confidence
    scored.sort((a, b) => b.confidence - a.confidence);

    const best = scored[0];

    // Check for ambiguous matches
    if (
      scored.length > 1 &&
      scored[1].confidence > 0.8 &&
      Math.abs(best.confidence - scored[1].confidence) < 0.1
    ) {
      this.logger.warn({
        event: 'ambiguous_match',
        invoice_line_id: line.id,
        candidates: scored.slice(0, 3),
      });

      return {
        invoice_line_id: line.id,
        shipment_id: best.shipment_id,
        confidence: best.confidence,
        match_type: 'fuzzy',
        match_criteria: best.match_criteria,
        issues: ['Multiple similar matches found'],
      };
    }

    // Require minimum confidence
    if (best.confidence < 0.7) {
      return {
        invoice_line_id: line.id,
        shipment_id: null,
        confidence: best.confidence,
        match_type: 'unmatched',
        match_criteria: best.match_criteria,
        issues: ['Confidence below threshold'],
      };
    }

    return best;
  }

  /**
   * Find candidate shipments for matching
   */
  private async findCandidateShipments(
    line: InvoiceLine,
    baseQuery: any,
  ): Promise<Shipment[]> {
    const query = baseQuery.clone();

    // Date window: ±3 days
    if (line.shipment_date) {
      const dateFrom = new Date(line.shipment_date);
      dateFrom.setDate(dateFrom.getDate() - 3);

      const dateTo = new Date(line.shipment_date);
      dateTo.setDate(dateTo.getDate() + 3);

      query.andWhere('shipment.date BETWEEN :dateFrom AND :dateTo', {
        dateFrom,
        dateTo,
      });
    }

    // Origin/destination filters
    if (line.origin_zip) {
      query.andWhere('shipment.origin_zip LIKE :originZip', {
        originZip: `${line.origin_zip.substring(0, 3)}%`,
      });
    }

    if (line.dest_zip) {
      query.andWhere('shipment.dest_zip LIKE :destZip', {
        destZip: `${line.dest_zip.substring(0, 3)}%`,
      });
    }

    // Weight range: ±20%
    if (line.weight_kg) {
      const weightMin = line.weight_kg * 0.8;
      const weightMax = line.weight_kg * 1.2;

      query.andWhere(
        'shipment.weight_kg BETWEEN :weightMin AND :weightMax',
        {
          weightMin,
          weightMax,
        },
      );
    }

    // Limit to reasonable number of candidates
    query.limit(10);

    return query.getMany();
  }

  /**
   * Score a potential match between line and shipment
   */
  private scoreMatch(
    line: InvoiceLine,
    shipment: Shipment,
  ): MatchResult {
    let score = 0;
    const criteria: string[] = [];

    // Date match (25%)
    if (line.shipment_date && shipment.date) {
      const daysDiff = Math.abs(
        (line.shipment_date.getTime() - shipment.date.getTime()) /
          (1000 * 60 * 60 * 24),
      );

      if (daysDiff === 0) {
        score += 0.25;
        criteria.push('date_exact');
      } else if (daysDiff <= 1) {
        score += 0.2;
        criteria.push('date_1day');
      } else if (daysDiff <= 3) {
        score += 0.15;
        criteria.push('date_3days');
      }
    }

    // Origin match (20%)
    if (line.origin_zip && shipment.origin_zip) {
      if (line.origin_zip === shipment.origin_zip) {
        score += 0.2;
        criteria.push('origin_exact');
      } else if (
        line.origin_zip.substring(0, 3) ===
        shipment.origin_zip.substring(0, 3)
      ) {
        score += 0.15;
        criteria.push('origin_prefix');
      }
    }

    // Destination match (20%)
    if (line.dest_zip && shipment.dest_zip) {
      if (line.dest_zip === shipment.dest_zip) {
        score += 0.2;
        criteria.push('dest_exact');
      } else if (
        line.dest_zip.substring(0, 3) ===
        shipment.dest_zip.substring(0, 3)
      ) {
        score += 0.15;
        criteria.push('dest_prefix');
      }
    }

    // Weight match (15%)
    if (line.weight_kg && shipment.weight_kg) {
      const weightDiff =
        Math.abs(line.weight_kg - shipment.weight_kg) /
        line.weight_kg;

      if (weightDiff < 0.05) {
        score += 0.15;
        criteria.push('weight_exact');
      } else if (weightDiff < 0.1) {
        score += 0.12;
        criteria.push('weight_close');
      } else if (weightDiff < 0.2) {
        score += 0.08;
        criteria.push('weight_similar');
      }
    }

    // Amount match (20%)
    if (line.line_total && shipment.actual_total_amount) {
      const amountDiff =
        Math.abs(line.line_total - shipment.actual_total_amount) /
        line.line_total;

      if (amountDiff < 0.01) {
        score += 0.2;
        criteria.push('amount_exact');
      } else if (amountDiff < 0.05) {
        score += 0.15;
        criteria.push('amount_close');
      } else if (amountDiff < 0.1) {
        score += 0.1;
        criteria.push('amount_similar');
      }
    }

    // Service level match (bonus 5%)
    if (line.service_level && shipment.service_level) {
      if (
        line.service_level.toLowerCase() ===
        shipment.service_level.toLowerCase()
      ) {
        score += 0.05;
        criteria.push('service_level');
      }
    }

    // Determine match type
    let matchType: 'exact' | 'fuzzy' | 'manual' | 'unmatched';
    if (score >= 0.95) {
      matchType = 'exact';
    } else if (score >= 0.7) {
      matchType = 'fuzzy';
    } else {
      matchType = 'unmatched';
    }

    return {
      invoice_line_id: line.id,
      shipment_id: shipment.id,
      confidence: Math.min(score, 1.0),
      match_type: matchType,
      match_criteria: criteria,
    };
  }

  /**
   * Manually match an invoice line to a shipment
   */
  async manualMatch(
    lineId: string,
    shipmentId: string,
    tenantId: string,
  ): Promise<void> {
    // Verify both belong to tenant
    const line = await this.lineRepo.findOne({
      where: { id: lineId, tenant_id: tenantId },
    });

    const shipment = await this.shipmentRepo.findOne({
      where: { id: shipmentId, tenant_id: tenantId },
    });

    if (!line || !shipment) {
      throw new Error('Invoice line or shipment not found');
    }

    // Update match
    await this.lineRepo.update(lineId, {
      shipment_id: shipmentId,
      match_status: 'manual',
      match_confidence: 1.0,
      meta: {
        ...line.meta,
        match_criteria: ['manual'],
        matched_at: new Date().toISOString(),
      },
    });

    this.logger.log({
      event: 'manual_match_created',
      invoice_line_id: lineId,
      shipment_id: shipmentId,
    });
  }

  /**
   * Unmatch an invoice line
   */
  async unmatch(lineId: string, tenantId: string): Promise<void> {
    await this.lineRepo.update(
      { id: lineId, tenant_id: tenantId },
      {
        shipment_id: null,
        match_status: 'unmatched',
        match_confidence: 0,
        meta: {},
      },
    );

    this.logger.log({
      event: 'match_removed',
      invoice_line_id: lineId,
    });
  }

  /**
   * Get matching statistics for a project
   */
  async getProjectMatchingStats(
    projectId: string,
    tenantId: string,
  ): Promise<MatchingStats> {
    const lines = await this.lineRepo
      .createQueryBuilder('line')
      .leftJoin('line.invoice', 'invoice')
      .where('invoice.project_id = :projectId', { projectId })
      .andWhere('invoice.tenant_id = :tenantId', { tenantId })
      .getMany();

    let matched = 0;
    let unmatched = 0;
    let ambiguous = 0;
    let manual = 0;
    let confidenceSum = 0;

    for (const line of lines) {
      switch (line.match_status) {
        case 'matched':
        case 'exact':
        case 'fuzzy':
          matched++;
          confidenceSum += line.match_confidence || 0;
          break;
        case 'manual':
          manual++;
          confidenceSum += line.match_confidence || 1.0;
          break;
        case 'ambiguous':
          ambiguous++;
          confidenceSum += line.match_confidence || 0;
          break;
        default:
          unmatched++;
      }
    }

    return {
      total_lines: lines.length,
      matched,
      unmatched,
      ambiguous,
      manual,
      avg_confidence:
        lines.length > 0 ? confidenceSum / lines.length : 0,
    };
  }
}
