import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Shipment } from '../parsing/entities/shipment.entity';
import { ShipmentBenchmark } from '../tariff/entities/shipment-benchmark.entity';
import { round } from '../../utils/round';

/**
 * Aggregated data for a carrier
 */
export interface CarrierAggregation {
  carrier_id: string;
  carrier_name: string;
  shipment_count: number;
  total_actual_cost: number;
  total_expected_cost: number;
  total_delta: number;
  avg_delta_pct: number;
  overpay_count: number;
  underpay_count: number;
  market_count: number;
  data_completeness_avg: number;
}

/**
 * Overall project statistics
 */
export interface ProjectStatistics {
  total_shipments: number;
  parsed_shipments: number;
  benchmarked_shipments: number;
  complete_shipments: number;
  partial_shipments: number;
  missing_shipments: number;
  data_completeness_avg: number;
  total_actual_cost: number;
  total_expected_cost: number;
  total_savings_potential: number;
  overpay_rate: number;
  carriers: CarrierAggregation[];
}

/**
 * ReportAggregationService - Data Aggregation Logic
 *
 * Calculates aggregated statistics for reports:
 * - Project-level summaries
 * - Carrier-level breakdowns
 * - Overpay detection metrics
 * - Data quality metrics
 */
@Injectable()
export class ReportAggregationService {
  private readonly logger = new Logger(ReportAggregationService.name);

  constructor(
    @InjectRepository(Shipment)
    private readonly shipmentRepo: Repository<Shipment>,
    @InjectRepository(ShipmentBenchmark)
    private readonly benchmarkRepo: Repository<ShipmentBenchmark>,
  ) {}

  /**
   * Calculate project-level statistics
   */
  async calculateProjectStatistics(
    projectId: string,
    tenantId: string,
  ): Promise<ProjectStatistics> {
    this.logger.log({
      event: 'calculate_project_statistics_start',
      project_id: projectId,
    });

    // Load all shipments for project
    const shipments = await this.shipmentRepo.find({
      where: { project_id: projectId, tenant_id: tenantId },
      relations: ['carrier'],
    });

    // Load all benchmarks
    const benchmarks = await this.benchmarkRepo.find({
      where: {
        tenant_id: tenantId,
      },
      relations: ['shipment'],
    });

    // Filter by project_id if needed
    const filtered = benchmarks.filter(b => b.shipment?.project_id === projectId);

    // Create benchmark map for quick lookup
    const benchmarkMap = new Map<string, ShipmentBenchmark>();
    for (const benchmark of filtered) {
      benchmarkMap.set(benchmark.shipment.id, benchmark);
    }

    // Calculate overall stats
    let completeCount = 0;
    let partialCount = 0;
    let missingCount = 0;
    let totalActual = 0;
    let totalExpected = 0;
    let totalDelta = 0;
    let completenessSum = 0;
    let overpayCount = 0;

    for (const shipment of shipments) {
      // Completeness tracking
      const completeness = shipment.completeness_score ?? 0;
      completenessSum += completeness;

      if (completeness >= 0.9) {
        completeCount++;
      } else if (completeness >= 0.5) {
        partialCount++;
      } else {
        missingCount++;
      }

      // Cost tracking
      const benchmark = benchmarkMap.get(shipment.id);
      if (benchmark) {
        totalActual += shipment.actual_total_amount ?? 0;
        totalExpected += benchmark.expected_total_amount ?? 0;
        totalDelta += benchmark.delta_amount ?? 0;

        if (benchmark.classification === 'drüber') {
          overpayCount++;
        }
      }
    }

    const avgCompleteness = shipments.length > 0
      ? round(completenessSum / shipments.length)
      : 0;

    const overpayRate = shipments.length > 0
      ? round((overpayCount / shipments.length) * 100)
      : 0;

    // Calculate carrier-level aggregations
    const carriers = await this.aggregateByCarrier(
      shipments,
      benchmarkMap,
      tenantId,
    );

    const statistics: ProjectStatistics = {
      total_shipments: shipments.length,
      parsed_shipments: shipments.length, // All in DB are parsed
      benchmarked_shipments: benchmarks.length,
      complete_shipments: completeCount,
      partial_shipments: partialCount,
      missing_shipments: missingCount,
      data_completeness_avg: avgCompleteness,
      total_actual_cost: round(totalActual),
      total_expected_cost: round(totalExpected),
      total_savings_potential: round(totalDelta),
      overpay_rate: overpayRate,
      carriers,
    };

    this.logger.log({
      event: 'calculate_project_statistics_complete',
      project_id: projectId,
      total_shipments: statistics.total_shipments,
      overpay_rate: statistics.overpay_rate,
    });

    return statistics;
  }

  /**
   * Aggregate shipments by carrier
   */
  private async aggregateByCarrier(
    shipments: Shipment[],
    benchmarkMap: Map<string, ShipmentBenchmark>,
    tenantId: string,
  ): Promise<CarrierAggregation[]> {
    // Group by carrier
    const carrierMap = new Map<string, {
      name: string;
      shipments: Shipment[];
    }>();

    for (const shipment of shipments) {
      if (!shipment.carrier_id) continue;

      if (!carrierMap.has(shipment.carrier_id)) {
        carrierMap.set(shipment.carrier_id, {
          name: shipment.carrier?.name || 'Unknown',
          shipments: [],
        });
      }

      carrierMap.get(shipment.carrier_id)!.shipments.push(shipment);
    }

    // Calculate stats per carrier
    const results: CarrierAggregation[] = [];

    for (const [carrierId, data] of carrierMap.entries()) {
      let totalActual = 0;
      let totalExpected = 0;
      let totalDelta = 0;
      let deltaPctSum = 0;
      let overpayCount = 0;
      let underpayCount = 0;
      let marketCount = 0;
      let completenessSum = 0;
      let benchmarkedCount = 0;

      for (const shipment of data.shipments) {
        const benchmark = benchmarkMap.get(shipment.id);

        if (benchmark) {
          totalActual += shipment.actual_total_amount ?? 0;
          totalExpected += benchmark.expected_total_amount ?? 0;
          totalDelta += benchmark.delta_amount ?? 0;
          deltaPctSum += benchmark.delta_pct ?? 0;
          benchmarkedCount++;

          switch (benchmark.classification) {
            case 'drüber':
              overpayCount++;
              break;
            case 'unter':
              underpayCount++;
              break;
            case 'im_markt':
              marketCount++;
              break;
          }
        }

        completenessSum += shipment.completeness_score ?? 0;
      }

      const avgDeltaPct = benchmarkedCount > 0
        ? round(deltaPctSum / benchmarkedCount)
        : 0;

      const avgCompleteness = data.shipments.length > 0
        ? round(completenessSum / data.shipments.length)
        : 0;

      results.push({
        carrier_id: carrierId,
        carrier_name: data.name,
        shipment_count: data.shipments.length,
        total_actual_cost: round(totalActual),
        total_expected_cost: round(totalExpected),
        total_delta: round(totalDelta),
        avg_delta_pct: avgDeltaPct,
        overpay_count: overpayCount,
        underpay_count: underpayCount,
        market_count: marketCount,
        data_completeness_avg: avgCompleteness,
      });
    }

    // Sort by total delta descending (highest overpay first)
    results.sort((a, b) => b.total_delta - a.total_delta);

    return results;
  }

  /**
   * Calculate data completeness for project
   */
  async calculateDataCompleteness(
    projectId: string,
    tenantId: string,
  ): Promise<number> {
    const shipments = await this.shipmentRepo.find({
      where: { project_id: projectId, tenant_id: tenantId },
      select: ['completeness_score'],
    });

    if (shipments.length === 0) return 0;

    const sum = shipments.reduce(
      (acc, s) => acc + (s.completeness_score ?? 0),
      0,
    );

    return round(sum / shipments.length);
  }

  /**
   * Get top overpay shipments for project
   */
  async getTopOverpays(
    projectId: string,
    tenantId: string,
    limit: number = 10,
  ): Promise<ShipmentBenchmark[]> {
    const benchmarks = await this.benchmarkRepo
      .createQueryBuilder('benchmark')
      .leftJoinAndSelect('benchmark.shipment', 'shipment')
      .leftJoinAndSelect('shipment.carrier', 'carrier')
      .where('shipment.project_id = :projectId', { projectId })
      .andWhere('shipment.tenant_id = :tenantId', { tenantId })
      .andWhere("benchmark.classification = 'drüber'")
      .orderBy('benchmark.delta_amount', 'DESC')
      .limit(limit)
      .getMany();

    return benchmarks;
  }

  /**
   * Get date range for shipments in project
   */
  async getDateRange(
    projectId: string,
    tenantId: string,
  ): Promise<{ start_date: Date | null; end_date: Date | null }> {
    const result = await this.shipmentRepo
      .createQueryBuilder('s')
      .select('MIN(s.date)', 'start_date')
      .addSelect('MAX(s.date)', 'end_date')
      .where('s.project_id = :projectId', { projectId })
      .andWhere('s.tenant_id = :tenantId', { tenantId })
      .getRawOne();

    return {
      start_date: result.start_date || null,
      end_date: result.end_date || null,
    };
  }
}
