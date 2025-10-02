import { Injectable } from '@nestjs/common';

/**
 * ServiceMapperService - Simplified version (Phase 2 Refactoring)
 *
 * NO DATABASE LOOKUPS - pure fuzzy matching
 * This service normalizes service level text to standard codes using pattern matching.
 *
 * Replaced service_alias/service_catalog tables with enum-only matching.
 */
@Injectable()
export class ServiceMapperService {

  /**
   * Normalize service text to standard service code
   * NO DATABASE LOOKUPS - pure fuzzy matching
   */
  async normalize(serviceText: string): Promise<string> {
    if (!serviceText) {
      return 'STANDARD';
    }

    const normalized = serviceText.toLowerCase().trim();

    // Express patterns
    if (/express|24h|next.*day|overnight|eilsendung|schnell/i.test(normalized)) {
      return 'EXPRESS';
    }

    // Same Day
    if (/same.*day|sameday/i.test(normalized)) {
      return 'SAME_DAY';
    }

    // Economy
    if (/eco|economy|slow|spar|günstig|cheap|sparversand|langsam/i.test(normalized)) {
      return 'ECONOMY';
    }

    // Premium
    if (/premium|priority|first.*class|firstclass/i.test(normalized)) {
      return 'PREMIUM';
    }

    // Standard (default)
    return 'STANDARD';
  }

  /**
   * Bulk normalize - process multiple service texts at once
   */
  async bulkNormalize(serviceTexts: string[]): Promise<Map<string, string>> {
    const results = new Map<string, string>();

    for (const text of serviceTexts) {
      if (!results.has(text)) {
        const normalized = await this.normalize(text);
        results.set(text, normalized);
      }
    }

    return results;
  }
}