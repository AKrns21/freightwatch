import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, LessThanOrEqual, MoreThanOrEqual, IsNull, Or } from 'typeorm';
import { TariffZoneMap } from './entities/tariff-zone-map.entity';

@Injectable()
export class ZoneCalculatorService {
  private readonly logger = new Logger(ZoneCalculatorService.name);

  constructor(
    @InjectRepository(TariffZoneMap)
    private readonly tariffZoneMapRepository: Repository<TariffZoneMap>,
  ) {}

  async calculateZone(
    tenantId: string,
    carrierId: string,
    country: string,
    destZip: string,
    date: Date,
  ): Promise<number> {
    if (!destZip || destZip.trim() === '') {
      throw new Error('Destination ZIP code is required for zone calculation');
    }

    const normalizedZip = destZip.trim().toUpperCase();
    const normalizedCountry = country.trim().toUpperCase();

    this.logger.debug(
      `Calculating zone for tenant ${tenantId}, carrier ${carrierId}, country ${normalizedCountry}, zip ${normalizedZip}, date ${date.toISOString()}`,
    );

    try {
      let zone = await this.tryPrefixMatching(
        tenantId,
        carrierId,
        normalizedCountry,
        normalizedZip,
        date,
      );

      if (zone !== null) {
        this.logger.debug(
          `Found zone ${zone} via prefix matching for ${normalizedCountry}-${normalizedZip}`,
        );
        return zone;
      }

      zone = await this.tryPatternMatching(
        tenantId,
        carrierId,
        normalizedCountry,
        normalizedZip,
        date,
      );

      if (zone !== null) {
        this.logger.debug(
          `Found zone ${zone} via pattern matching for ${normalizedCountry}-${normalizedZip}`,
        );
        return zone;
      }

      throw new NotFoundException(
        `No zone mapping found for carrier ${carrierId}, country ${normalizedCountry}, ZIP ${normalizedZip}`,
      );
    } catch (error) {
      if (error instanceof NotFoundException) {
        throw error;
      }

      this.logger.error(
        `Error calculating zone for ${normalizedCountry}-${normalizedZip}: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw new Error(`Zone calculation failed: ${(error as Error).message}`);
    }
  }

  private async tryPrefixMatching(
    tenantId: string,
    carrierId: string,
    country: string,
    destZip: string,
    date: Date,
  ): Promise<number | null> {
    for (let prefixLen = Math.min(5, destZip.length); prefixLen >= 2; prefixLen--) {
      const prefix = destZip.substring(0, prefixLen);

      try {
        const mapping = await this.tariffZoneMapRepository.findOne({
          where: {
            tenant_id: tenantId,
            carrier_id: carrierId,
            country: country,
            plz_prefix: prefix,
            prefix_len: prefixLen,
            valid_from: LessThanOrEqual(date),
            valid_until: Or(MoreThanOrEqual(date), IsNull()),
          },
          order: {
            valid_from: 'DESC', // Get most recent mapping if multiple exist
          },
        });

        if (mapping) {
          this.logger.debug(
            `Prefix match found: ${prefix} (length ${prefixLen}) -> zone ${mapping.zone}`,
          );
          return mapping.zone;
        }
      } catch (error) {
        this.logger.warn(
          `Error in prefix matching for ${prefix}: ${(error as Error).message}`,
        );
      }
    }

    return null;
  }

  private async tryPatternMatching(
    tenantId: string,
    carrierId: string,
    country: string,
    destZip: string,
    date: Date,
  ): Promise<number | null> {
    try {
      const patternMappings = await this.tariffZoneMapRepository.find({
        where: {
          tenant_id: tenantId,
          carrier_id: carrierId,
          country: country,
          pattern: Not(IsNull()),
          valid_from: LessThanOrEqual(date),
          valid_until: Or(MoreThanOrEqual(date), IsNull()),
        },
        order: {
          valid_from: 'DESC',
        },
      });

      for (const mapping of patternMappings) {
        if (mapping.pattern) {
          try {
            const regex = new RegExp(mapping.pattern, 'i');
            if (regex.test(destZip)) {
              this.logger.debug(
                `Pattern match found: ${mapping.pattern} -> zone ${mapping.zone}`,
              );
              return mapping.zone;
            }
          } catch (regexError) {
            this.logger.warn(
              `Invalid regex pattern "${mapping.pattern}": ${(regexError as Error).message}`,
            );
          }
        }
      }
    } catch (error) {
      this.logger.warn(`Error in pattern matching: ${(error as Error).message}`);
    }

    return null;
  }

  async bulkCalculateZones(
    tenantId: string,
    carrierId: string,
    requests: Array<{
      country: string;
      destZip: string;
      date: Date;
    }>,
  ): Promise<Map<string, number>> {
    const results = new Map<string, number>();
    const cache = new Map<string, number>();

    for (const request of requests) {
      const cacheKey = `${request.country}-${request.destZip}-${request.date.toDateString()}`;
      
      if (cache.has(cacheKey)) {
        results.set(cacheKey, cache.get(cacheKey)!);
        continue;
      }

      try {
        const zone = await this.calculateZone(
          tenantId,
          carrierId,
          request.country,
          request.destZip,
          request.date,
        );
        
        results.set(cacheKey, zone);
        cache.set(cacheKey, zone);
      } catch (error) {
        this.logger.warn(
          `Failed to calculate zone for ${cacheKey}: ${(error as Error).message}`,
        );
        // Continue processing other requests even if one fails
      }
    }

    return results;
  }

  async getAvailableZones(
    tenantId: string,
    carrierId: string,
    country: string,
    date: Date,
  ): Promise<number[]> {
    try {
      const mappings = await this.tariffZoneMapRepository.find({
        where: {
          tenant_id: tenantId,
          carrier_id: carrierId,
          country: country.trim().toUpperCase(),
          valid_from: LessThanOrEqual(date),
          valid_until: Or(MoreThanOrEqual(date), IsNull()),
        },
        select: ['zone'],
      });

      const zones = [...new Set(mappings.map(m => m.zone))].sort();
      
      this.logger.debug(
        `Available zones for carrier ${carrierId}, country ${country}: [${zones.join(', ')}]`,
      );
      
      return zones;
    } catch (error) {
      this.logger.error(
        `Error fetching available zones: ${(error as Error).message}`,
        (error as Error).stack,
      );
      return [];
    }
  }
}

// Fix TypeORM import issue
import { Not } from 'typeorm';