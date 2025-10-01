import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, LessThanOrEqual } from 'typeorm';
import { FxRate } from './entities/fx-rate.entity';

@Injectable()
export class FxService {
  private readonly logger = new Logger(FxService.name);

  constructor(
    @InjectRepository(FxRate)
    private readonly fxRateRepository: Repository<FxRate>,
  ) {}

  async getRate(fromCcy: string, toCcy: string, date: Date): Promise<number> {
    const normalizedFromCcy = fromCcy.trim().toUpperCase();
    const normalizedToCcy = toCcy.trim().toUpperCase();

    if (normalizedFromCcy === normalizedToCcy) {
      return 1.0;
    }

    this.logger.debug(
      `Getting FX rate: ${normalizedFromCcy} -> ${normalizedToCcy} on ${date.toISOString()}`,
    );

    try {
      const directRate = await this.findDirectRate(normalizedFromCcy, normalizedToCcy, date);
      if (directRate !== null) {
        this.logger.debug(
          `Found direct rate: ${normalizedFromCcy}/${normalizedToCcy} = ${directRate}`,
        );
        return directRate;
      }

      const inverseRate = await this.findInverseRate(normalizedFromCcy, normalizedToCcy, date);
      if (inverseRate !== null) {
        this.logger.debug(
          `Found inverse rate: ${normalizedToCcy}/${normalizedFromCcy} = ${inverseRate}, returning ${1.0 / inverseRate}`,
        );
        return 1.0 / inverseRate;
      }

      throw new NotFoundException(
        `No FX rate found for ${normalizedFromCcy}/${normalizedToCcy} on or before ${date.toISOString().split('T')[0]}`,
      );
    } catch (error) {
      if (error instanceof NotFoundException) {
        throw error;
      }

      this.logger.error(
        `Error getting FX rate ${normalizedFromCcy}/${normalizedToCcy}: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw new Error(`FX rate lookup failed: ${(error as Error).message}`);
    }
  }

  private async findDirectRate(fromCcy: string, toCcy: string, date: Date): Promise<number | null> {
    try {
      const fxRate = await this.fxRateRepository.findOne({
        where: {
          from_ccy: fromCcy,
          to_ccy: toCcy,
          rate_date: LessThanOrEqual(date),
        },
        order: {
          rate_date: 'DESC',
        },
      });

      return fxRate ? Number(fxRate.rate) : null;
    } catch (error) {
      this.logger.warn(
        `Error in direct rate lookup ${fromCcy}/${toCcy}: ${(error as Error).message}`,
      );
      return null;
    }
  }

  private async findInverseRate(fromCcy: string, toCcy: string, date: Date): Promise<number | null> {
    try {
      const fxRate = await this.fxRateRepository.findOne({
        where: {
          from_ccy: toCcy,
          to_ccy: fromCcy,
          rate_date: LessThanOrEqual(date),
        },
        order: {
          rate_date: 'DESC',
        },
      });

      return fxRate ? Number(fxRate.rate) : null;
    } catch (error) {
      this.logger.warn(
        `Error in inverse rate lookup ${toCcy}/${fromCcy}: ${(error as Error).message}`,
      );
      return null;
    }
  }

  async bulkGetRates(
    requests: Array<{
      fromCcy: string;
      toCcy: string;
      date: Date;
    }>,
  ): Promise<Map<string, number>> {
    const results = new Map<string, number>();
    const cache = new Map<string, number>();

    for (const request of requests) {
      const cacheKey = `${request.fromCcy}-${request.toCcy}-${request.date.toDateString()}`;
      
      if (cache.has(cacheKey)) {
        results.set(cacheKey, cache.get(cacheKey)!);
        continue;
      }

      try {
        const rate = await this.getRate(request.fromCcy, request.toCcy, request.date);
        results.set(cacheKey, rate);
        cache.set(cacheKey, rate);
      } catch (error) {
        this.logger.warn(
          `Failed to get FX rate for ${cacheKey}: ${(error as Error).message}`,
        );
        // Continue processing other requests even if one fails
      }
    }

    return results;
  }

  async seedCommonRates(source: string): Promise<void> {
    const seedDate = new Date('2023-01-01');
    const rates = [
      { fromCcy: 'EUR', toCcy: 'CHF', rate: 0.9850 },
      { fromCcy: 'EUR', toCcy: 'USD', rate: 1.0650 },
      { fromCcy: 'EUR', toCcy: 'GBP', rate: 0.8850 },
      { fromCcy: 'EUR', toCcy: 'PLN', rate: 4.6800 },
    ];

    this.logger.log(`Seeding common FX rates with source: ${source}`);

    try {
      for (const rateData of rates) {
        const existingRate = await this.fxRateRepository.findOne({
          where: {
            rate_date: seedDate,
            from_ccy: rateData.fromCcy,
            to_ccy: rateData.toCcy,
          },
        });

        if (!existingRate) {
          const fxRate = this.fxRateRepository.create({
            rate_date: seedDate,
            from_ccy: rateData.fromCcy,
            to_ccy: rateData.toCcy,
            rate: rateData.rate,
            source: source,
          });

          await this.fxRateRepository.save(fxRate);
          
          this.logger.debug(
            `Seeded rate: ${rateData.fromCcy}/${rateData.toCcy} = ${rateData.rate}`,
          );
        } else {
          this.logger.debug(
            `Rate already exists: ${rateData.fromCcy}/${rateData.toCcy} on ${seedDate.toISOString().split('T')[0]}`,
          );
        }
      }

      this.logger.log(`Successfully seeded ${rates.length} FX rates`);
    } catch (error) {
      this.logger.error(
        `Error seeding common FX rates: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }

  async addRate(
    fromCcy: string,
    toCcy: string,
    rate: number,
    date: Date,
    source: string,
  ): Promise<void> {
    const normalizedFromCcy = fromCcy.trim().toUpperCase();
    const normalizedToCcy = toCcy.trim().toUpperCase();

    if (normalizedFromCcy === normalizedToCcy) {
      throw new Error('Cannot add FX rate for same currency pair');
    }

    if (rate <= 0) {
      throw new Error('FX rate must be positive');
    }

    try {
      const fxRate = this.fxRateRepository.create({
        rate_date: date,
        from_ccy: normalizedFromCcy,
        to_ccy: normalizedToCcy,
        rate: rate,
        source: source,
      });

      await this.fxRateRepository.save(fxRate);
      
      this.logger.log(
        `Added FX rate: ${normalizedFromCcy}/${normalizedToCcy} = ${rate} on ${date.toISOString().split('T')[0]} (${source})`,
      );
    } catch (error) {
      this.logger.error(
        `Error adding FX rate: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }

  async getAvailableCurrencies(date?: Date): Promise<string[]> {
    try {
      const query = this.fxRateRepository
        .createQueryBuilder('fx')
        .select('DISTINCT fx.from_ccy', 'currency')
        .union(
          this.fxRateRepository
            .createQueryBuilder('fx')
            .select('DISTINCT fx.to_ccy', 'currency'),
        );

      if (date) {
        query.where('fx.rate_date <= :date', { date });
      }

      const result = await query.orderBy('currency').getRawMany();
      const currencies = result.map(row => row.currency).filter(Boolean);
      
      // Add EUR as base currency if not present
      if (!currencies.includes('EUR')) {
        currencies.unshift('EUR');
      }

      return currencies.sort();
    } catch (error) {
      this.logger.error(
        `Error getting available currencies: ${(error as Error).message}`,
        (error as Error).stack,
      );
      return ['EUR']; // Fallback to EUR only
    }
  }
}