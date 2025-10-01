import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { ServiceAlias } from './entities/service-alias.entity';

@Injectable()
export class ServiceMapperService {
  private readonly logger = new Logger(ServiceMapperService.name);

  constructor(
    @InjectRepository(ServiceAlias)
    private readonly serviceAliasRepository: Repository<ServiceAlias>,
  ) {}

  async normalize(tenantId: string, carrierId: string | null, serviceText: string): Promise<string> {
    if (!serviceText || serviceText.trim() === '') {
      return 'STANDARD';
    }

    const normalizedText = serviceText.toLowerCase().trim();

    try {
      let serviceCode: string | null = null;

      serviceCode = await this.lookupTenantSpecific(tenantId, normalizedText);
      if (serviceCode) {
        this.logger.debug(`Tenant-specific mapping: "${serviceText}" -> ${serviceCode}`);
        return serviceCode;
      }

      if (carrierId) {
        serviceCode = await this.lookupCarrierSpecific(carrierId, normalizedText);
        if (serviceCode) {
          this.logger.debug(`Carrier-specific mapping: "${serviceText}" -> ${serviceCode}`);
          return serviceCode;
        }
      }

      serviceCode = await this.lookupGlobal(normalizedText);
      if (serviceCode) {
        this.logger.debug(`Global mapping: "${serviceText}" -> ${serviceCode}`);
        return serviceCode;
      }

      const fuzzyResult = this.fuzzyMatch(normalizedText);
      this.logger.debug(`Fuzzy matching: "${serviceText}" -> ${fuzzyResult}`);
      return fuzzyResult;

    } catch (error) {
      this.logger.error(
        `Error normalizing service "${serviceText}": ${(error as Error).message}`,
        (error as Error).stack,
      );
      return this.fuzzyMatch(normalizedText);
    }
  }

  private async lookupTenantSpecific(tenantId: string, aliasText: string): Promise<string | null> {
    try {
      const alias = await this.serviceAliasRepository.findOne({
        where: {
          tenant_id: tenantId,
          alias_text: aliasText,
        },
      });

      return alias?.service_code || null;
    } catch (error) {
      this.logger.warn(`Error in tenant-specific lookup: ${(error as Error).message}`);
      return null;
    }
  }

  private async lookupCarrierSpecific(carrierId: string, aliasText: string): Promise<string | null> {
    try {
      const alias = await this.serviceAliasRepository.findOne({
        where: {
          tenant_id: null,
          carrier_id: carrierId,
          alias_text: aliasText,
        },
      });

      return alias?.service_code || null;
    } catch (error) {
      this.logger.warn(`Error in carrier-specific lookup: ${(error as Error).message}`);
      return null;
    }
  }

  private async lookupGlobal(aliasText: string): Promise<string | null> {
    try {
      const alias = await this.serviceAliasRepository.findOne({
        where: {
          tenant_id: null,
          carrier_id: null,
          alias_text: aliasText,
        },
      });

      return alias?.service_code || null;
    } catch (error) {
      this.logger.warn(`Error in global lookup: ${(error as Error).message}`);
      return null;
    }
  }

  private fuzzyMatch(normalizedText: string): string {
    const patterns = [
      {
        regex: /express|24h|overnight|next.*day|eilsendung|schnell/i,
        service: 'EXPRESS',
      },
      {
        regex: /same.*day|sameday/i,
        service: 'SAME_DAY',
      },
      {
        regex: /eco|economy|slow|spar|günstig|cheap|sparversand|langsam/i,
        service: 'ECONOMY',
      },
      {
        regex: /premium|priority|first.*class|firstclass/i,
        service: 'PREMIUM',
      },
      {
        regex: /standard|normal|regular|default|standardversand|normalversand/i,
        service: 'STANDARD',
      },
    ];

    for (const pattern of patterns) {
      if (pattern.regex.test(normalizedText)) {
        return pattern.service;
      }
    }

    return 'STANDARD';
  }

  async bulkNormalize(
    tenantId: string,
    carrierId: string | null,
    serviceTexts: string[],
  ): Promise<Map<string, string>> {
    const results = new Map<string, string>();

    for (const serviceText of serviceTexts) {
      if (!results.has(serviceText)) {
        const normalized = await this.normalize(tenantId, carrierId, serviceText);
        results.set(serviceText, normalized);
      }
    }

    return results;
  }

  async addTenantAlias(
    tenantId: string,
    aliasText: string,
    serviceCode: string,
  ): Promise<void> {
    try {
      const alias = this.serviceAliasRepository.create({
        tenant_id: tenantId,
        carrier_id: null,
        alias_text: aliasText.toLowerCase().trim(),
        service_code: serviceCode,
      });

      await this.serviceAliasRepository.save(alias);
      
      this.logger.log(
        `Added tenant-specific alias for tenant ${tenantId}: "${aliasText}" -> ${serviceCode}`,
      );
    } catch (error) {
      this.logger.error(
        `Failed to add tenant alias: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }

  async addCarrierAlias(
    carrierId: string,
    aliasText: string,
    serviceCode: string,
  ): Promise<void> {
    try {
      const alias = this.serviceAliasRepository.create({
        tenant_id: null,
        carrier_id: carrierId,
        alias_text: aliasText.toLowerCase().trim(),
        service_code: serviceCode,
      });

      await this.serviceAliasRepository.save(alias);
      
      this.logger.log(
        `Added carrier-specific alias for carrier ${carrierId}: "${aliasText}" -> ${serviceCode}`,
      );
    } catch (error) {
      this.logger.error(
        `Failed to add carrier alias: ${(error as Error).message}`,
        (error as Error).stack,
      );
      throw error;
    }
  }
}