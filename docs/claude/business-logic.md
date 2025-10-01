# Business Logic Guide

This document describes the core business logic for freight cost calculations, tariff engine, and overpay detection.

## Tariff Engine Overview

The Tariff Engine calculates **expected costs** for shipments and compares them against **actual costs** from invoices to identify overpayment.

### Calculation Pipeline

```
Input: Shipment (date, carrier, origin, dest, weight, ldm, actual_cost)
  ↓
1. Determine Lane Type (domestic_de, de_to_ch, ...)
2. Calculate Zone (PLZ → Zone mapping)
3. Calculate Chargeable Weight (kg vs LDM)
4. Find Tariff (zone + weight range + date)
5. Convert Currency (if needed)
6. Add Diesel Surcharge (% based on date)
7. Add Toll (actual or estimated)
8. Calculate Expected Total
9. Calculate Delta (actual - expected)
10. Classify (unter/im_markt/drüber)
  ↓
Output: Benchmark (expected_total, delta, classification)
```

## 1. Lane Type Determination

Lane type defines the routing logic and which tariff table to use.

### Implementation

```typescript
export class LaneCalculator {
  determineLane(
    originCountry: string,
    destCountry: string
  ): string {
    // Domestic
    if (originCountry === destCountry) {
      return `domestic_${originCountry.toLowerCase()}`;
    }
    
    // International
    const [from, to] = [originCountry, destCountry].sort();
    return `${from.toLowerCase()}_to_${to.toLowerCase()}`;
  }
}

// Examples:
// ('DE', 'DE') → 'domestic_de'
// ('AT', 'AT') → 'domestic_at'
// ('DE', 'CH') → 'de_to_ch'
// ('CH', 'DE') → 'ch_to_de'
// ('AT', 'DE') → 'at_to_de'
```

### Lane Types

Common lanes:
- `domestic_de` - Germany domestic
- `domestic_at` - Austria domestic
- `de_to_ch` - Germany to Switzerland
- `de_to_at` - Germany to Austria
- `at_to_de` - Austria to Germany
- `ch_to_de` - Switzerland to Germany

**Important:** Tariffs are directional. `de_to_ch` ≠ `ch_to_de`.

## 2. Zone Calculation

Zones group postal codes into pricing regions. Each carrier has its own zone mapping.

### Zone Lookup Logic

```typescript
export class ZoneCalculator {
  async calculateZone(
    tenantId: string,
    carrierId: string,
    country: string,
    zipCode: string,
    date: Date
  ): Promise<number | null> {
    // Try prefix matching first (e.g., "80" for "80331")
    let zone = await this.tryPrefixMatch(
      tenantId, 
      carrierId, 
      country, 
      zipCode, 
      date
    );
    
    if (zone) return zone;
    
    // Fallback to pattern matching (e.g., regex "^80.*")
    zone = await this.tryPatternMatch(
      tenantId, 
      carrierId, 
      country, 
      zipCode, 
      date
    );
    
    return zone;
  }
  
  private async tryPrefixMatch(
    tenantId: string,
    carrierId: string,
    country: string,
    zipCode: string,
    date: Date
  ): Promise<number | null> {
    // Try from longest to shortest prefix
    for (let len = zipCode.length; len >= 1; len--) {
      const prefix = zipCode.substring(0, len);
      
      const result = await this.db.query(`
        SELECT zone
        FROM tariff_zone_map
        WHERE tenant_id = $1
          AND carrier_id = $2
          AND country = $3
          AND plz_prefix = $4
          AND prefix_len = $5
          AND valid_from <= $6
          AND (valid_until IS NULL OR valid_until >= $6)
        ORDER BY valid_from DESC
        LIMIT 1
      `, [tenantId, carrierId, country, prefix, len, date]);
      
      if (result.rows.length > 0) {
        return result.rows[0].zone;
      }
    }
    
    return null;
  }
  
  private async tryPatternMatch(
    tenantId: string,
    carrierId: string,
    country: string,
    zipCode: string,
    date: Date
  ): Promise<number | null> {
    const result = await this.db.query(`
      SELECT zone
      FROM tariff_zone_map
      WHERE tenant_id = $1
        AND carrier_id = $2
        AND country = $3
        AND pattern IS NOT NULL
        AND $4 ~ pattern
        AND valid_from <= $6
        AND (valid_until IS NULL OR valid_until >= $6)
      ORDER BY valid_from DESC
      LIMIT 1
    `, [tenantId, carrierId, country, zipCode, date]);
    
    return result.rows[0]?.zone ?? null;
  }
}
```

### Zone Map Examples

**Germany (typical 1-6 zone system):**
```sql
INSERT INTO tariff_zone_map (tenant_id, carrier_id, country, plz_prefix, prefix_len, zone, valid_from)
VALUES
  ('tenant-uuid', 'carrier-uuid', 'DE', '8', 1, 1, '2023-01-01'),  -- Zone 1: 80000-89999
  ('tenant-uuid', 'carrier-uuid', 'DE', '9', 1, 2, '2023-01-01'),  -- Zone 2: 90000-99999
  ('tenant-uuid', 'carrier-uuid', 'DE', '0', 1, 3, '2023-01-01'),  -- Zone 3: 00000-09999
  -- ...
```

**Austria (9 zones):**
```sql
INSERT INTO tariff_zone_map (tenant_id, carrier_id, country, plz_prefix, prefix_len, zone, valid_from)
VALUES
  ('tenant-uuid', 'carrier-uuid', 'AT', '1', 1, 1, '2023-01-01'),  -- Vienna
  ('tenant-uuid', 'carrier-uuid', 'AT', '2', 1, 2, '2023-01-01'),  -- Eastern Austria
  -- ...
```

## 3. Chargeable Weight Calculation

Freight is billed on the greater of:
- **Actual weight** (kg)
- **Volumetric weight** (Lademeter * conversion factor)

### Implementation

```typescript
export class WeightCalculator {
  async calculateChargeableWeight(
    tenantId: string,
    carrierId: string,
    weightKg: number,
    ldm: number | null
  ): Promise<number> {
    // No LDM? Use actual weight
    if (!ldm || ldm === 0) {
      return round(weightKg);
    }
    
    // Load LDM → kg conversion factor from tariff_rule
    const rule = await this.ruleRepo.findOne({
      tenant_id: tenantId,
      carrier_id: carrierId,
      rule_type: 'ldm_conversion'
    });
    
    if (!rule) {
      // CRITICAL: No default! Log warning and use actual weight
      this.logger.warn({
        event: 'missing_ldm_rule',
        tenant_id: tenantId,
        carrier_id: carrierId,
        message: 'No ldm_conversion rule found, using actual weight'
      });
      return round(weightKg);
    }
    
    const ldmToKg = rule.param_json.ldm_to_kg;
    const volumetricWeight = ldm * ldmToKg;
    
    return round(Math.max(weightKg, volumetricWeight));
  }
}

// Example:
// weightKg = 800, ldm = 1.5, ldm_to_kg = 1850
// volumetricWeight = 1.5 * 1850 = 2775
// chargeableWeight = MAX(800, 2775) = 2775
```

### Tariff Rule Format

```sql
INSERT INTO tariff_rule (tenant_id, carrier_id, rule_type, param_json)
VALUES 
  ('tenant-uuid', 'carrier-a', 'ldm_conversion', '{"ldm_to_kg": 1850}'),
  ('tenant-uuid', 'carrier-b', 'ldm_conversion', '{"ldm_to_kg": 1900}'),
  ('tenant-uuid', 'carrier-c', 'min_pallet_weight', '{"min_kg_per_pallet": 300}');
```

**Important:** Different carriers may have different conversion factors. Never hardcode!

## 4. Tariff Lookup

Find the applicable base cost for a shipment.

### Implementation

```typescript
export class TariffService {
  async findTariff(
    tenantId: string,
    carrierId: string,
    laneType: string,
    zone: number,
    chargeableWeight: number,
    date: Date
  ): Promise<Tariff | null> {
    const result = await this.db.query(`
      SELECT 
        id,
        base_amount,
        currency,
        weight_min,
        weight_max,
        valid_from,
        valid_until
      FROM tariff_table
      WHERE tenant_id = $1
        AND carrier_id = $2
        AND lane_type = $3
        AND zone = $4
        AND $5 BETWEEN weight_min AND weight_max
        AND valid_from <= $6
        AND (valid_until IS NULL OR valid_until >= $6)
      ORDER BY valid_from DESC
      LIMIT 1
    `, [
      tenantId,
      carrierId,
      laneType,
      zone,
      chargeableWeight,
      date
    ]);
    
    return result.rows[0] ?? null;
  }
}
```

### Weight Bands Example

```sql
INSERT INTO tariff_table (
  tenant_id, carrier_id, lane_type, zone, 
  weight_min, weight_max, base_amount, currency, valid_from
)
VALUES
  -- Zone 1, 0-50kg
  ('tenant-uuid', 'carrier-uuid', 'domestic_de', 1, 0, 50, 25.00, 'EUR', '2023-01-01'),
  
  -- Zone 1, 51-100kg
  ('tenant-uuid', 'carrier-uuid', 'domestic_de', 1, 51, 100, 35.00, 'EUR', '2023-01-01'),
  
  -- Zone 1, 101-500kg
  ('tenant-uuid', 'carrier-uuid', 'domestic_de', 1, 101, 500, 75.00, 'EUR', '2023-01-01'),
  
  -- Zone 1, 501+kg
  ('tenant-uuid', 'carrier-uuid', 'domestic_de', 1, 501, 99999, 150.00, 'EUR', '2023-01-01');
```

**Note:** Use `weight_max = 99999` for the highest band (effectively unlimited).

## 5. Currency Conversion

Convert tariff base_amount to shipment currency if they differ.

### Implementation

```typescript
export class FXService {
  async convert(
    amount: number,
    fromCurrency: string,
    toCurrency: string,
    date: Date
  ): Promise<{ amount: number; rate: number; rateDate: Date }> {
    // Same currency? No conversion needed
    if (fromCurrency === toCurrency) {
      return { 
        amount: round(amount), 
        rate: 1.0, 
        rateDate: date 
      };
    }
    
    // Load FX rate for date
    const fx = await this.fxRepo.findOne({
      from_ccy: fromCurrency,
      to_ccy: toCurrency,
      rate_date: date
    });
    
    if (!fx) {
      // Try previous day (fallback for weekends/holidays)
      const prevDay = new Date(date);
      prevDay.setDate(prevDay.getDate() - 1);
      
      const fallback = await this.fxRepo.findOne({
        from_ccy: fromCurrency,
        to_ccy: toCurrency,
        rate_date: prevDay
      });
      
      if (!fallback) {
        throw new Error(
          `No FX rate found for ${fromCurrency}→${toCurrency} on ${date}`
        );
      }
      
      this.logger.warn({
        event: 'fx_rate_fallback',
        from: fromCurrency,
        to: toCurrency,
        requested_date: date,
        used_date: prevDay
      });
      
      return {
        amount: round(amount * fallback.rate),
        rate: fallback.rate,
        rateDate: prevDay
      };
    }
    
    return {
      amount: round(amount * fx.rate),
      rate: fx.rate,
      rateDate: fx.rate_date
    };
  }
}
```

### FX Rate Examples

```sql
INSERT INTO fx_rate (from_ccy, to_ccy, rate, rate_date, source)
VALUES
  ('CHF', 'EUR', 1.0234, '2023-12-01', 'ECB'),
  ('CHF', 'EUR', 1.0198, '2023-12-02', 'ECB'),
  ('USD', 'EUR', 0.9123, '2023-12-01', 'ECB'),
  ('GBP', 'EUR', 1.1567, '2023-12-01', 'ECB');
```

## 6. Diesel Surcharge

Diesel surcharges fluctuate based on fuel prices. They are applied as a percentage of a **basis**.

### Diesel Basis Types

1. **`base`**: Diesel = base_amount * pct
   - Most common
   - Example: Base €100, diesel 15% → €15

2. **`base_plus_toll`**: Diesel = (base_amount + toll) * pct
   - Some carriers include toll in diesel basis
   - Example: Base €100, toll €20, diesel 15% → (100+20) * 0.15 = €18

3. **`total`**: Diesel = total_amount * pct
   - Rare, but some carriers do this
   - Example: Total €130, diesel 10% → €13

### Implementation

```typescript
export class DieselService {
  async calculateDiesel(
    tenantId: string,
    carrierId: string,
    baseAmount: number,
    tollAmount: number,
    date: Date
  ): Promise<{ amount: number; pct: number; basis: string }> {
    // Load diesel floater for date
    const floater = await this.db.query(`
      SELECT pct, basis
      FROM diesel_floater
      WHERE tenant_id = $1
        AND carrier_id = $2
        AND valid_from <= $3
        AND (valid_until IS NULL OR valid_until >= $3)
      ORDER BY valid_from DESC
      LIMIT 1
    `, [tenantId, carrierId, date]);
    
    if (floater.rows.length === 0) {
      throw new Error(
        `No diesel floater found for carrier ${carrierId} on ${date}`
      );
    }
    
    const { pct, basis } = floater.rows[0];
    let dieselAmount: number;
    
    switch (basis) {
      case 'base':
        dieselAmount = baseAmount * (pct / 100);
        break;
      
      case 'base_plus_toll':
        dieselAmount = (baseAmount + tollAmount) * (pct / 100);
        break;
      
      case 'total':
        // Note: 'total' is tricky - we don't have total yet
        // This is rare, log warning
        this.logger.warn({
          event: 'diesel_basis_total',
          carrier_id: carrierId,
          date,
          message: 'Using base+toll as approximation for total basis'
        });
        dieselAmount = (baseAmount + tollAmount) * (pct / 100);
        break;
      
      default:
        throw new Error(`Unknown diesel basis: ${basis}`);
    }
    
    return {
      amount: round(dieselAmount),
      pct,
      basis
    };
  }
}
```

### Diesel Floater Examples

```sql
-- DHL: 18% on base, valid from Jan 1 - Mar 31
INSERT INTO diesel_floater (
  tenant_id, carrier_id, 
  valid_from, valid_until, 
  pct, basis
)
VALUES 
  ('tenant-uuid', 'dhl-uuid', '2023-01-01', '2023-03-31', 18.0, 'base');

-- DHL: Increased to 22% starting April 1
INSERT INTO diesel_floater (
  tenant_id, carrier_id, 
  valid_from, valid_until, 
  pct, basis
)
VALUES 
  ('tenant-uuid', 'dhl-uuid', '2023-04-01', NULL, 22.0, 'base');
```

**Important:** Use `valid_until = NULL` for the current rate. Closing the interval happens when adding the next rate.

## 7. Toll Calculation

Toll (Maut) is charged for heavy goods transport. 

### Sources

1. **Actual toll** (from invoice) - preferred
2. **Estimated toll** (heuristic) - fallback

### Implementation

```typescript
export class TollService {
  calculateToll(
    shipment: Shipment
  ): { amount: number; source: string } {
    // Prefer actual toll from invoice
    if (shipment.toll_amount && shipment.toll_amount > 0) {
      return {
        amount: round(shipment.toll_amount),
        source: 'actual'
      };
    }
    
    // Fallback: Estimate based on weight
    // Note: This is a rough heuristic. 3.5t is the vehicle class threshold,
    // NOT necessarily the shipment weight threshold.
    if (shipment.weight_kg >= 3500) {
      const estimatedToll = this.estimateToll(
        shipment.origin_zip,
        shipment.dest_zip,
        shipment.weight_kg
      );
      
      return {
        amount: round(estimatedToll),
        source: 'estimated_heuristic'
      };
    }
    
    // No toll
    return { amount: 0, source: 'none' };
  }
  
  private estimateToll(
    originZip: string,
    destZip: string,
    weightKg: number
  ): number {
    // Rough estimate: €0.18/km for >7.5t, €0.12/km for 3.5-7.5t
    const distance = this.estimateDistance(originZip, destZip);
    const ratePerKm = weightKg >= 7500 ? 0.18 : 0.12;
    
    return distance * ratePerKm;
  }
  
  private estimateDistance(originZip: string, destZip: string): number {
    // This should query a geo-distance service or lookup table
    // For MVP, use a simple heuristic based on zone distance
    // TODO: Integrate proper routing API
    return 250; // Placeholder
  }
}
```

**Important:** Mark estimated tolls clearly in `cost_breakdown` with `source: 'estimated_heuristic'` so users know it's not exact.

## 8. Expected Total Calculation

Combine all cost components.

### Implementation

```typescript
export class BenchmarkEngine {
  async calculateBenchmark(
    shipment: Shipment
  ): Promise<ShipmentBenchmark> {
    // 1. Calculate zone
    const zone = await this.zoneCalc.calculateZone(
      shipment.tenant_id,
      shipment.carrier_id,
      shipment.dest_country,
      shipment.dest_zip,
      shipment.date
    );
    
    if (!zone) {
      throw new Error(`Could not determine zone for ${shipment.dest_zip}`);
    }
    
    // 2. Calculate chargeable weight
    const chargeableWeight = await this.weightCalc.calculateChargeableWeight(
      shipment.tenant_id,
      shipment.carrier_id,
      shipment.weight_kg,
      shipment.ldm
    );
    
    // 3. Determine lane type
    const laneType = this.laneCalc.determineLane(
      shipment.origin_country,
      shipment.dest_country
    );
    
    // 4. Find tariff
    const tariff = await this.tariffService.findTariff(
      shipment.tenant_id,
      shipment.carrier_id,
      laneType,
      zone,
      chargeableWeight,
      shipment.date
    );
    
    if (!tariff) {
      throw new Error(`No tariff found for ${laneType} zone ${zone} ${chargeableWeight}kg`);
    }
    
    // 5. Convert currency
    const { amount: baseAmount, rate: fxRate, rateDate: fxRateDate } 
      = await this.fxService.convert(
        tariff.base_amount,
        tariff.currency,
        shipment.currency,
        shipment.date
      );
    
    // 6. Calculate toll
    const toll = this.tollService.calculateToll(shipment);
    
    // 7. Calculate diesel
    const diesel = await this.dieselService.calculateDiesel(
      shipment.tenant_id,
      shipment.carrier_id,
      baseAmount,
      toll.amount,
      shipment.date
    );
    
    // 8. Calculate expected total
    const expectedTotal = round(
      baseAmount + diesel.amount + toll.amount
    );
    
    // 9. Calculate delta
    const actualTotal = shipment.actual_total_amount;
    const delta = round(actualTotal - expectedTotal);
    const deltaPct = round((delta / expectedTotal) * 100);
    
    // 10. Classify
    let classification: string;
    if (deltaPct < -5) {
      classification = 'unter';
    } else if (deltaPct > 5) {
      classification = 'drüber';
    } else {
      classification = 'im_markt';
    }
    
    // 11. Create breakdown for audit trail
    const breakdown = {
      base: {
        amount: baseAmount,
        currency: shipment.currency,
        tariff_id: tariff.id,
        fx_rate: fxRate,
        fx_rate_date: fxRateDate
      },
      diesel: {
        amount: diesel.amount,
        pct: diesel.pct,
        basis: diesel.basis
      },
      toll: {
        amount: toll.amount,
        source: toll.source
      },
      total: {
        expected: expectedTotal,
        actual: actualTotal,
        delta: delta,
        delta_pct: deltaPct
      },
      metadata: {
        zone,
        lane_type: laneType,
        chargeable_weight: chargeableWeight,
        calc_version: '1.0'
      }
    };
    
    // 12. Return benchmark
    return {
      shipment_id: shipment.id,
      tenant_id: shipment.tenant_id,
      tariff_table_id: tariff.id,
      
      expected_base_amount: baseAmount,
      expected_diesel_amount: diesel.amount,
      expected_toll_amount: toll.amount,
      expected_total_amount: expectedTotal,
      
      actual_total_amount: actualTotal,
      delta_amount: delta,
      delta_pct: deltaPct,
      
      classification,
      
      diesel_basis_used: diesel.basis,
      diesel_pct_used: diesel.pct,
      fx_rate_used: fxRate,
      fx_rate_date: fxRateDate,
      
      cost_breakdown: breakdown,
      calc_version: '1.0'
    };
  }
}
```

## 9. Overpay Classification

Classify shipments based on delta percentage.

### Thresholds

- **`unter`** (underpay): delta_pct < -5%
  - Actual cost is significantly lower than expected
  - Possible reasons: Special discount, pricing error, manual adjustment

- **`im_markt`** (market rate): -5% ≤ delta_pct ≤ 5%
  - Actual cost matches expected (within tolerance)
  - Normal variation

- **`drüber`** (overpay): delta_pct > 5%
  - **THIS IS THE MONEY MAKER**
  - Actual cost is significantly higher than expected
  - Investigation targets for cost reduction

### Why ±5%?

- Tariffs are approximations (weight bands, zones)
- Small surcharges may not be in tariff (insurance, COD, etc.)
- Currency conversion rounding
- 5% is generous enough to avoid false positives

**Adjust threshold via tenant settings if needed:**
```sql
UPDATE tenant 
SET settings = settings || '{"overpay_threshold_pct": 3}'::jsonb
WHERE id = 'tenant-uuid';
```

## 10. Service Level Normalization

Carriers use different service names for equivalent delivery speeds.

### Normalization Logic

```typescript
export class ServiceMapper {
  async normalize(
    tenantId: string,
    carrierId: string | null,
    serviceText: string | null
  ): Promise<string | null> {
    if (!serviceText) return null;
    
    const normalized = serviceText.toLowerCase().trim();
    
    // Try tenant-specific alias
    if (tenantId) {
      const alias = await this.aliasRepo.findOne({
        tenant_id: tenantId,
        carrier_id: carrierId,
        alias_text: normalized
      });
      
      if (alias) return alias.service_code;
    }
    
    // Try carrier-specific alias
    if (carrierId) {
      const alias = await this.aliasRepo.findOne({
        tenant_id: null,
        carrier_id: carrierId,
        alias_text: normalized
      });
      
      if (alias) return alias.service_code;
    }
    
    // Try global alias
    const alias = await this.aliasRepo.findOne({
      tenant_id: null,
      carrier_id: null,
      alias_text: normalized
    });
    
    if (alias) return alias.service_code;
    
    // No match found - log and return original
    this.logger.warn({
      event: 'service_unmapped',
      tenant_id: tenantId,
      carrier_id: carrierId,
      service_text: serviceText
    });
    
    return serviceText;
  }
}
```

### Service Catalog

```sql
INSERT INTO service_catalog (code, description, category) VALUES
  ('STANDARD', 'Standard Delivery', 'standard'),
  ('EXPRESS', 'Express/Next Day', 'premium'),
  ('ECONOMY', 'Economy/Slow', 'economy'),
  ('NEXT_DAY', 'Next Day Delivery', 'premium'),
  ('SAME_DAY', 'Same Day Delivery', 'premium'),
  ('PREMIUM', 'Premium Service', 'premium');
```

### Alias Examples

```sql
-- Global aliases
INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES
  (NULL, NULL, '24h', 'EXPRESS'),
  (NULL, NULL, 'next day', 'NEXT_DAY'),
  (NULL, NULL, 'overnight', 'NEXT_DAY'),
  (NULL, NULL, 'express', 'EXPRESS'),
  (NULL, NULL, 'standard', 'STANDARD'),
  (NULL, NULL, 'normal', 'STANDARD'),
  (NULL, NULL, 'eco', 'ECONOMY');

-- DHL-specific
INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES
  (NULL, 'dhl-uuid', 'premium', 'PREMIUM'),
  (NULL, 'dhl-uuid', 'paket', 'STANDARD');

-- Tenant-specific (overrides global)
INSERT INTO service_alias (tenant_id, carrier_id, alias_text, service_code) VALUES
  ('tenant-uuid', 'carrier-uuid', 'rush', 'SAME_DAY');
```

## Business Rules Summary

### MUST Follow

1. **Never hardcode business logic** - Load from `tariff_rule` table
2. **Always use `round()` from `utils/round.ts`** - Deterministic calculations
3. **Log missing data warnings** - Don't fail silently
4. **Preserve audit trail** - Store `cost_breakdown` JSONB
5. **Mark estimates** - Use `source: 'estimated_heuristic'` for fallbacks
6. **Currency agnostic** - Never assume EUR
7. **Tenant-scoped** - All calculations within tenant context

### Performance Targets

- **Parsing**: >90% success rate
- **Tariff match**: >85% coverage
- **Benchmark accuracy**: >80% within ±5%
- **Processing speed**: <30s for 10k shipments

### Error Handling

```typescript
// ✅ GOOD: Fail fast with context
if (!zone) {
  throw new BusinessRuleError({
    code: 'ZONE_NOT_FOUND',
    message: `Could not determine zone for ${shipment.dest_zip}`,
    context: {
      shipment_id: shipment.id,
      carrier_id: shipment.carrier_id,
      dest_zip: shipment.dest_zip,
      dest_country: shipment.dest_country
    }
  });
}

// ❌ BAD: Silent failure
if (!zone) {
  zone = 1; // Guessing zone 1 - WRONG!
}
```

## References

- Tariff calculation logic: `src/modules/tariff/engines/tariff-engine.ts`
- Zone calculator: `src/modules/tariff/engines/zone-calculator.ts`
- Diesel service: `src/modules/tariff/services/diesel-service.ts`
- FX service: `src/modules/tariff/engines/fx-service.ts`