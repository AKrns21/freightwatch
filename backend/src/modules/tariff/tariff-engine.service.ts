import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, LessThanOrEqual, Or, IsNull, MoreThanOrEqual, LessThan, In } from 'typeorm';
import { TariffTable } from './entities/tariff-table.entity';
import { TariffRate } from './entities/tariff-rate.entity';
import { DieselFloater } from './entities/diesel-floater.entity';
import { ShipmentBenchmark } from './entities/shipment-benchmark.entity';
import { Carrier } from '../upload/entities/carrier.entity';
import { ZoneCalculatorService } from './zone-calculator.service';
import { FxService } from './fx.service';
import { Shipment } from '../parsing/entities/shipment.entity';
import { BenchmarkResult, CostBreakdownItem } from './interfaces/benchmark-result.interface';
import { round } from '../../utils/round';

/**
 * TariffEngineService - Phase 2 Refactored
 *
 * Changes:
 * - Removed TariffRule entity/repository (table dropped in migration 003)
 * - Added Carrier repository to access conversion_rules JSONB
 * - Business rules now loaded from carrier.conversion_rules instead of tariff_rule table
 */
@Injectable()
export class TariffEngineService {
  private readonly logger = new Logger(TariffEngineService.name);

  constructor(
    @InjectRepository(TariffTable)
    private readonly tariffTableRepository: Repository<TariffTable>,
    @InjectRepository(TariffRate)
    private readonly tariffRateRepository: Repository<TariffRate>,
    @InjectRepository(DieselFloater)
    private readonly dieselFloaterRepository: Repository<DieselFloater>,
    @InjectRepository(ShipmentBenchmark)
    private readonly shipmentBenchmarkRepository: Repository<ShipmentBenchmark>,
    @InjectRepository(Carrier)
    private readonly carrierRepository: Repository<Carrier>,
    private readonly zoneCalculatorService: ZoneCalculatorService,
    private readonly fxService: FxService,
  ) {}

  async calculateExpectedCost(shipment: Shipment): Promise<BenchmarkResult> {
    this.logger.debug(
      `Calculating expected cost for shipment ${shipment.id} (${shipment.weight_kg}kg, ${shipment.origin_country}-${shipment.dest_country})`,
    );

    try {
      const laneType = this.determineLaneType(
        shipment.origin_country || 'DE',
        shipment.dest_country || 'DE',
      );

      const zone = await this.calculateZone(shipment, laneType);

      const applicableTariff = await this.findApplicableTariff(
        shipment.tenant_id,
        shipment.carrier_id,
        laneType,
        shipment.date,
      );

      const chargeableWeightResult = await this.calculateChargeableWeight(
        shipment.tenant_id,
        shipment.carrier_id,
        shipment,
      );

      const tariffRate = await this.findTariffRate(
        applicableTariff.id,
        zone,
        chargeableWeightResult.value,
      );

      const baseAmount = this.calculateBaseAmount(tariffRate, chargeableWeightResult.value);

      const convertedAmount = await this.convertCurrency(
        baseAmount,
        applicableTariff.currency,
        shipment.currency,
        shipment.date,
      );

      // Calculate toll amount
      let tollAmount: number;
      let tollNote: string;
      
      if (shipment.toll_amount && shipment.toll_amount > 0) {
        // Use actual toll amount from invoice
        tollAmount = shipment.toll_amount;
        tollNote = 'from_invoice';
        this.logger.debug(`Using invoice toll amount: ${tollAmount} ${shipment.currency}`);
      } else {
        // Estimate toll using heuristic
        const estimatedToll = this.estimateToll(
          zone, 
          chargeableWeightResult.value, 
          shipment.dest_country || 'DE'
        );
        
        // Convert estimated toll to shipment currency if needed
        const convertedToll = await this.convertCurrency(
          estimatedToll,
          applicableTariff.currency,
          shipment.currency,
          shipment.date,
        );
        
        tollAmount = convertedToll.amount;
        tollNote = 'estimated_heuristic';
        this.logger.debug(`Estimated toll amount: ${tollAmount} ${shipment.currency} (zone: ${zone}, weight: ${chargeableWeightResult.value}kg)`);
      }

      // Get diesel floater and calculate diesel surcharge
      const dieselFloater = await this.getDieselFloater(
        shipment.tenant_id,
        shipment.carrier_id,
        shipment.date,
      );

      let dieselBase: number;
      switch (dieselFloater.basis) {
        case 'base':
          dieselBase = convertedAmount.amount;
          break;
        case 'base_plus_toll':
          dieselBase = convertedAmount.amount + tollAmount;
          break;
        case 'total':
          // For MVP: ignore circular dependency, use base cost + toll
          dieselBase = convertedAmount.amount + tollAmount;
          break;
        default:
          dieselBase = convertedAmount.amount;
          break;
      }

      const dieselAmount = round(dieselBase * (dieselFloater.pct / 100));
      const totalAmount = convertedAmount.amount + tollAmount + dieselAmount;

      const costBreakdown: CostBreakdownItem[] = [
        {
          item: 'base_rate',
          description: `Zone ${zone} base rate (${chargeableWeightResult.basis})`,
          zone: zone,
          weight: chargeableWeightResult.value,
          rate: tariffRate.rate_per_shipment || tariffRate.rate_per_kg || 0,
          amount: convertedAmount.amount,
          currency: shipment.currency,
          note: chargeableWeightResult.note + (convertedAmount.fx_note ? `. ${convertedAmount.fx_note}` : ''),
        },
        {
          item: 'toll',
          description: `Toll charges (${tollNote})`,
          value: tollAmount,
          amount: tollAmount,
          currency: shipment.currency,
          note: tollNote,
        },
        {
          item: 'diesel_surcharge',
          description: `Diesel surcharge (${dieselFloater.pct}% on ${dieselFloater.basis})`,
          base: dieselBase,
          pct: dieselFloater.pct,
          value: dieselAmount,
          amount: dieselAmount,
          currency: shipment.currency,
        },
      ];

      // Calculate delta and classification
      const expectedTotal = round(totalAmount);
      const actualTotal = shipment.actual_total_amount || 0;
      const deltaAmount = round(actualTotal - expectedTotal);
      const deltaPct = expectedTotal > 0 ? round((deltaAmount / expectedTotal) * 100) : 0;
      
      let classification: string;
      if (deltaPct < -5) {
        classification = 'unter';
      } else if (deltaPct > 5) {
        classification = 'drüber';
      } else {
        classification = 'im_markt';
      }

      // For MVP: use EUR as default tenant currency
      const tenantCurrency = 'EUR';
      let reportAmounts: any = null;
      let reportFxRate: number | null = null;

      // Reporting currency conversion if needed
      if (shipment.currency !== tenantCurrency) {
        try {
          reportFxRate = await this.fxService.getRate(shipment.currency, tenantCurrency, shipment.date);
          
          reportAmounts = {
            expected_base_amount: round(convertedAmount.amount * reportFxRate),
            expected_toll_amount: round(tollAmount * reportFxRate),
            expected_diesel_amount: round(dieselAmount * reportFxRate),
            expected_total_amount: round(expectedTotal * reportFxRate),
            actual_total_amount: round(actualTotal * reportFxRate),
            delta_amount: round(deltaAmount * reportFxRate),
            currency: tenantCurrency,
          };

          this.logger.debug(
            `Converted amounts to tenant currency ${tenantCurrency} using rate ${reportFxRate}`,
          );
        } catch (error) {
          this.logger.warn(
            `Failed to convert to tenant currency ${tenantCurrency}: ${(error as Error).message}`,
          );
        }
      }

      const result: BenchmarkResult = {
        expected_base_amount: round(convertedAmount.amount),
        expected_toll_amount: round(tollAmount),
        expected_diesel_amount: dieselAmount,
        expected_total_amount: expectedTotal,
        actual_total_amount: actualTotal,
        delta_amount: deltaAmount,
        delta_pct: deltaPct,
        classification,
        cost_breakdown: costBreakdown,
        report_amounts: reportAmounts,
        calculation_metadata: {
          tariff_table_id: applicableTariff.id,
          lane_type: laneType,
          zone_calculated: zone,
          fx_rate_used: convertedAmount.fx_rate,
          fx_rate_date: convertedAmount.fx_rate ? shipment.date : undefined,
          diesel_basis_used: dieselFloater.basis,
          diesel_pct_used: dieselFloater.pct,
          calc_version: '1.4-complete-benchmark',
        },
      };

      // Create shipment benchmark record
      if (shipment.id) {
        await this.createShipmentBenchmark(shipment, result, reportFxRate, tenantCurrency);
      }

      this.logger.debug(
        `Calculated expected cost: ${result.expected_total_amount} ${shipment.currency} for shipment ${shipment.id} (delta: ${deltaAmount}, ${deltaPct}%, ${classification})`,
      );

      return result;
    } catch (error) {
      this.logger.error(
        `Error calculating expected cost for shipment ${shipment.id}: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }

  private async getDieselFloater(
    tenantId: string,
    carrierId: string,
    date: Date,
  ): Promise<{ pct: number; basis: string }> {
    try {
      const dieselFloater = await this.dieselFloaterRepository.findOne({
        where: {
          tenant_id: tenantId,
          carrier_id: carrierId,
          valid_from: LessThanOrEqual(date),
          valid_until: Or(MoreThanOrEqual(date), IsNull()),
        },
        order: {
          valid_from: 'DESC',
        },
      });

      if (dieselFloater) {
        this.logger.debug(
          `Found diesel floater: ${dieselFloater.floater_pct}% (basis: ${dieselFloater.basis})`,
        );
        return {
          pct: Number(dieselFloater.floater_pct),
          basis: dieselFloater.basis,
        };
      }

      // Fallback to default diesel floater
      this.logger.warn({
        event: 'diesel_floater_fallback',
        tenant_id: tenantId,
        carrier_id: carrierId,
        date: date.toISOString().split('T')[0],
        message: 'No diesel floater found, using default 18.5%',
      });

      return {
        pct: 18.5, // Default fallback
        basis: 'base',
      };
    } catch (error) {
      this.logger.error(
        `Error finding diesel floater: ${(error as Error).message}`,
        (error as Error).stack,
      );
      
      // Return fallback on error
      return {
        pct: 18.5,
        basis: 'base',
      };
    }
  }

  private async createShipmentBenchmark(
    shipment: Shipment,
    result: BenchmarkResult,
    reportFxRate: number | null,
    tenantCurrency: string,
  ): Promise<void> {
    try {
      const benchmark = new ShipmentBenchmark();
      benchmark.shipment_id = shipment.id;
      benchmark.tenant_id = shipment.tenant_id;
      benchmark.expected_base_amount = result.expected_base_amount;
      benchmark.expected_toll_amount = result.expected_toll_amount || null;
      benchmark.expected_diesel_amount = result.expected_diesel_amount || null;
      benchmark.expected_total_amount = result.expected_total_amount;
      benchmark.actual_total_amount = result.actual_total_amount || 0;
      benchmark.delta_amount = result.delta_amount || 0;
      benchmark.delta_pct = result.delta_pct || 0;
      benchmark.classification = result.classification || 'im_markt';
      benchmark.currency = shipment.currency;
      benchmark.report_currency = shipment.currency !== tenantCurrency ? tenantCurrency : null;
      benchmark.fx_rate_used = reportFxRate;
      benchmark.fx_rate_date = reportFxRate ? shipment.date : null;
      benchmark.diesel_basis_used = result.calculation_metadata.diesel_basis_used || null;
      benchmark.diesel_pct_used = result.calculation_metadata.diesel_pct_used || null;
      benchmark.cost_breakdown = result.cost_breakdown;
      benchmark.report_amounts = result.report_amounts;
      benchmark.calculation_metadata = result.calculation_metadata;

      await this.shipmentBenchmarkRepository.save(benchmark);

      this.logger.debug(
        `Created shipment benchmark record for shipment ${shipment.id}`,
      );
    } catch (error) {
      this.logger.error(
        `Error creating shipment benchmark record: ${(error as Error).message}`,
        (error as Error).stack,
      );
      // Don't throw error - benchmark creation is not critical for the main flow
    }
  }

  private estimateToll(zone: number, weightKg: number, country: string): number {
    // NOTE: This 3.5t threshold is a vehicle class heuristic (truck vs van),
    // not directly applicable to shipment weight. Use as rough estimate for MVP.
    if (weightKg < 3500) return 0;

    const tollByCountry: { [key: string]: { [zone: number]: number } } = {
      'DE': { 1: 5, 2: 8, 3: 12, 4: 15, 5: 18, 6: 15 },
      'AT': { 1: 6, 2: 10, 3: 14, 4: 18, 5: 22, 6: 18 },
      'CH': { 1: 8, 2: 12, 3: 16, 4: 20, 5: 24, 6: 20 },
      'FR': { 1: 7, 2: 11, 3: 15, 4: 19, 5: 23, 6: 19 },
    };

    return tollByCountry[country]?.[zone] || 0;
  }

  private determineLaneType(originCountry: string, destCountry: string): string {
    const origin = originCountry.toUpperCase();
    const dest = destCountry.toUpperCase();

    if (origin === 'DE' && dest === 'DE') {
      return 'DE';
    }

    if ((origin === 'DE' && dest === 'AT') || (origin === 'AT' && dest === 'DE')) {
      return 'AT';
    }

    if ((origin === 'DE' && dest === 'CH') || (origin === 'CH' && dest === 'DE')) {
      return 'CH';
    }

    if (['DE', 'AT', 'CH', 'FR', 'IT', 'NL', 'BE', 'PL'].includes(origin) &&
        ['DE', 'AT', 'CH', 'FR', 'IT', 'NL', 'BE', 'PL'].includes(dest)) {
      return 'EU';
    }

    return 'EXPORT';
  }

  private async calculateZone(shipment: Shipment, laneType: string): Promise<number> {
    try {
      const zone = await this.zoneCalculatorService.calculateZone(
        shipment.tenant_id,
        shipment.carrier_id || '',
        shipment.dest_country || 'DE',
        shipment.dest_zip || '',
        shipment.date,
      );

      this.logger.debug(
        `Calculated zone ${zone} for ${shipment.dest_country}-${shipment.dest_zip}`,
      );

      return zone;
    } catch (error) {
      this.logger.warn(
        `Zone calculation failed for ${shipment.dest_country}-${shipment.dest_zip}: ${(error as Error).message}`,
      );
      
      // Fallback to default zone based on lane type
      const defaultZone = laneType === 'DE' ? 1 : 3;
      this.logger.debug(`Using fallback zone: ${defaultZone}`);
      
      return defaultZone;
    }
  }

  private async findApplicableTariff(
    tenantId: string,
    carrierId: string | null,
    laneType: string,
    date: Date,
  ): Promise<TariffTable> {
    try {
      const tariff = await this.tariffTableRepository.findOne({
        where: {
          tenant_id: tenantId,
          carrier_id: carrierId || undefined,
          lane_type: laneType,
          valid_from: LessThanOrEqual(date),
          valid_until: Or(MoreThanOrEqual(date), IsNull()),
        },
        order: {
          valid_from: 'DESC',
        },
      });

      if (!tariff) {
        throw new NotFoundException(
          `No applicable tariff found for tenant ${tenantId}, carrier ${carrierId}, lane ${laneType} on ${date.toISOString().split('T')[0]}`,
        );
      }

      this.logger.debug(`Found applicable tariff: ${tariff.name} (${tariff.id})`);
      return tariff;
    } catch (error) {
      this.logger.error(
        `Error finding applicable tariff: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }

  private async findTariffRate(
    tariffTableId: string,
    zone: number,
    weight: number,
  ): Promise<TariffRate> {
    try {
      const rate = await this.tariffRateRepository.findOne({
        where: {
          tariff_table_id: tariffTableId,
          zone: zone,
          weight_from_kg: weight >= 0 ? LessThanOrEqual(weight) : undefined,
          weight_to_kg: weight >= 0 ? MoreThanOrEqual(weight) : undefined,
        },
        order: {
          weight_from_kg: 'DESC', // Get most specific weight range
        },
      });

      if (!rate) {
        throw new NotFoundException(
          `No tariff rate found for zone ${zone}, weight ${weight}kg in tariff table ${tariffTableId}`,
        );
      }

      this.logger.debug(
        `Found tariff rate: Zone ${rate.zone}, Weight ${rate.weight_from_kg}-${rate.weight_to_kg}kg, Rate: ${rate.rate_per_shipment || rate.rate_per_kg}`,
      );

      return rate;
    } catch (error) {
      this.logger.error(
        `Error finding tariff rate: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }

  private calculateBaseAmount(tariffRate: TariffRate, weight: number): number {
    if (tariffRate.rate_per_shipment) {
      return Number(tariffRate.rate_per_shipment);
    }

    if (tariffRate.rate_per_kg) {
      return Number(tariffRate.rate_per_kg) * weight;
    }

    throw new Error(
      `Tariff rate ${tariffRate.id} has neither rate_per_shipment nor rate_per_kg`,
    );
  }

  private async convertCurrency(
    amount: number,
    fromCurrency: string,
    toCurrency: string,
    date: Date,
  ): Promise<{
    amount: number;
    fx_rate?: number;
    fx_note?: string;
  }> {
    if (fromCurrency === toCurrency) {
      return { amount };
    }

    try {
      const fxRate = await this.fxService.getRate(fromCurrency, toCurrency, date);
      const convertedAmount = amount * fxRate;

      return {
        amount: convertedAmount,
        fx_rate: fxRate,
        fx_note: `Converted from ${fromCurrency} using rate ${fxRate}`,
      };
    } catch (error) {
      this.logger.warn(
        `Currency conversion failed ${fromCurrency}->${toCurrency}: ${(error as Error).message}`,
      );

      // Return original amount if conversion fails
      return {
        amount,
        fx_note: `Conversion failed, using original ${fromCurrency} amount`,
      };
    }
  }

  private async calculateChargeableWeight(
    tenantId: string,
    carrierId: string | null,
    shipment: Shipment,
  ): Promise<{ value: number; basis: string; note: string }> {
    let maxWeight = shipment.weight_kg || 0;
    let basis = 'kg';
    const notes: string[] = [];

    if (maxWeight === 0) {
      return {
        value: 0,
        basis: 'kg',
        note: 'No weight provided',
      };
    }

    try {
      // Phase 2 Refactoring: Load conversion rules from carrier.conversion_rules JSONB
      // instead of tariff_rule table
      let conversionRules: Record<string, any> = {};

      if (carrierId) {
        const carrier = await this.carrierRepository.findOne({
          where: { id: carrierId },
        });

        if (carrier && carrier.conversion_rules) {
          conversionRules = carrier.conversion_rules;
          this.logger.debug(
            `Loaded conversion rules for carrier ${carrierId}:`,
            JSON.stringify(conversionRules),
          );
        } else {
          this.logger.debug(
            `No conversion rules found for carrier ${carrierId}, using defaults`,
          );
        }
      }

      // Apply LDM conversion rule if exists and length is provided
      const ldmConversionRule = conversionRules.ldm_conversion;
      if (ldmConversionRule && shipment.length_m && shipment.length_m > 0) {
        const ldmToKg = ldmConversionRule.ldm_to_kg;
        if (ldmToKg && typeof ldmToKg === 'number') {
          const minWeightFromLM = shipment.length_m * ldmToKg;

          this.logger.debug(
            `LDM conversion: ${shipment.length_m}m × ${ldmToKg} = ${minWeightFromLM}kg`,
          );

          if (minWeightFromLM > maxWeight) {
            maxWeight = minWeightFromLM;
            basis = 'lm';
            notes.push(`LDM weight: ${shipment.length_m}m × ${ldmToKg}kg/m = ${minWeightFromLM}kg`);
          } else {
            notes.push(`LDM weight ${minWeightFromLM}kg < actual weight, using actual`);
          }
        } else {
          this.logger.warn(`LDM conversion rule found but ldm_to_kg parameter invalid: ${ldmToKg}`);
        }
      }

      // Apply minimum pallet weight rule if exists and pallets are provided
      const palletWeightRule = conversionRules.min_pallet_weight;
      if (palletWeightRule && shipment.pallets && shipment.pallets > 0) {
        const minWeightPerPallet = palletWeightRule.min_kg_per_pallet;
        if (minWeightPerPallet && typeof minWeightPerPallet === 'number') {
          const minWeightFromPallets = shipment.pallets * minWeightPerPallet;

          this.logger.debug(
            `Pallet weight: ${shipment.pallets} × ${minWeightPerPallet}kg = ${minWeightFromPallets}kg`,
          );

          if (minWeightFromPallets > maxWeight) {
            maxWeight = minWeightFromPallets;
            basis = 'pallet';
            notes.push(`Pallet weight: ${shipment.pallets} × ${minWeightPerPallet}kg/pallet = ${minWeightFromPallets}kg`);
          } else {
            notes.push(`Pallet weight ${minWeightFromPallets}kg < chargeable weight, using current`);
          }
        } else {
          this.logger.warn(`Pallet weight rule found but min_kg_per_pallet parameter invalid: ${minWeightPerPallet}`);
        }
      }

      const finalNote = notes.length > 0
        ? notes.join('; ')
        : `Using actual weight: ${shipment.weight_kg}kg`;

      const result = {
        value: round(maxWeight),
        basis,
        note: finalNote,
      };

      this.logger.debug(
        `Chargeable weight calculated: ${result.value}kg (basis: ${result.basis})`,
      );

      return result;
    } catch (error) {
      this.logger.error(
        `Error calculating chargeable weight: ${(error as Error).message}`,
        (error as Error).stack,
      );

      // Fallback to actual weight on error
      return {
        value: round(maxWeight),
        basis: 'kg',
        note: `Error calculating chargeable weight, using actual: ${shipment.weight_kg}kg`,
      };
    }
  }

  /**
   * Calculate benchmarks for all shipments in a project
   * NEW: Wrapper with Partial Data Support
   */
  async calculateBenchmarkForProject(
    projectId: string,
    tenantId: string,
  ): Promise<void> {
    const shipments = await this.shipmentRepo.find({
      where: { project_id: projectId, tenant_id: tenantId },
    });

    this.logger.log({
      event: 'calculate_project_benchmarks_start',
      project_id: projectId,
      shipment_count: shipments.length,
    });

    let successCount = 0;
    let partialCount = 0;
    let errorCount = 0;

    for (const shipment of shipments) {
      try {
        // Use existing logic
        await this.calculateBenchmark(shipment.id, tenantId);

        // Update completeness
        await this.updateCompleteness(shipment.id, 1.0, []);
        successCount++;
      } catch (error) {
        this.logger.warn({
          event: 'partial_benchmark',
          shipment_id: shipment.id,
          error: (error as Error).message,
        });

        // Identify missing fields
        const missingFields = this.identifyMissingFields(shipment);

        if (missingFields.length > 0) {
          // Mark as partial
          await this.updateCompleteness(shipment.id, 0.5, missingFields);
          partialCount++;
        } else {
          // Error unrelated to missing data
          errorCount++;
        }
      }
    }

    this.logger.log({
      event: 'calculate_project_benchmarks_complete',
      project_id: projectId,
      success_count: successCount,
      partial_count: partialCount,
      error_count: errorCount,
    });
  }

  /**
   * Update shipment completeness tracking
   */
  private async updateCompleteness(
    shipmentId: string,
    score: number,
    missingFields: string[],
  ): Promise<void> {
    await this.shipmentRepo.update(shipmentId, {
      completeness_score: score,
      missing_fields: missingFields,
    });
  }

  /**
   * Identify which required fields are missing from a shipment
   */
  private identifyMissingFields(shipment: Shipment): string[] {
    const required = [
      'origin_zip',
      'dest_zip',
      'weight_kg',
      'carrier_id',
      'date',
      'actual_total_amount',
    ];

    const missing: string[] = [];

    for (const field of required) {
      const value = shipment[field];
      if (value === null || value === undefined || value === '') {
        missing.push(field);
      }
    }

    return missing;
  }
}