import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, LessThanOrEqual, Or, IsNull, MoreThanOrEqual, LessThan } from 'typeorm';
import { TariffTable } from './entities/tariff-table.entity';
import { TariffRate } from './entities/tariff-rate.entity';
import { ZoneCalculatorService } from './zone-calculator.service';
import { FxService } from './fx.service';
import { Shipment } from '../parsing/entities/shipment.entity';
import { BenchmarkResult, CostBreakdownItem } from './interfaces/benchmark-result.interface';
import { round } from '../../utils/round';

@Injectable()
export class TariffEngineService {
  private readonly logger = new Logger(TariffEngineService.name);

  constructor(
    @InjectRepository(TariffTable)
    private readonly tariffTableRepository: Repository<TariffTable>,
    @InjectRepository(TariffRate)
    private readonly tariffRateRepository: Repository<TariffRate>,
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

      const tariffRate = await this.findTariffRate(
        applicableTariff.id,
        zone,
        shipment.weight_kg || 0,
      );

      const baseAmount = this.calculateBaseAmount(tariffRate, shipment.weight_kg || 0);

      const convertedAmount = await this.convertCurrency(
        baseAmount,
        applicableTariff.currency,
        shipment.currency,
        shipment.date,
      );

      const costBreakdown: CostBreakdownItem[] = [
        {
          item: 'base_rate',
          description: `Zone ${zone} base rate`,
          zone: zone,
          weight: shipment.weight_kg || 0,
          rate: tariffRate.rate_per_shipment || tariffRate.rate_per_kg,
          amount: convertedAmount.amount,
          currency: shipment.currency,
          note: convertedAmount.fx_note,
        },
      ];

      const result: BenchmarkResult = {
        expected_base_amount: round(convertedAmount.amount),
        expected_total_amount: round(convertedAmount.amount),
        cost_breakdown: costBreakdown,
        calculation_metadata: {
          tariff_table_id: applicableTariff.id,
          lane_type: laneType,
          zone_calculated: zone,
          fx_rate_used: convertedAmount.fx_rate,
          fx_rate_date: convertedAmount.fx_rate ? shipment.date : undefined,
          calc_version: '1.0-base-only',
        },
      };

      this.logger.debug(
        `Calculated expected cost: ${result.expected_total_amount} ${shipment.currency} for shipment ${shipment.id}`,
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
}