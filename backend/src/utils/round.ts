/**
 * Deterministic rounding utilities for FreightWatch
 * 
 * Provides consistent financial rounding across the application
 * to avoid floating-point precision issues in cost calculations.
 */

export enum RoundingMode {
  HALF_UP = 'HALF_UP',     // Standard commercial rounding (0.5 rounds up)
  BANKERS = 'BANKERS'      // IEEE 754 (Round half to even)
}

/**
 * Rounds a number to 2 decimal places using the specified rounding mode
 * 
 * @param value - The number to round
 * @param mode - The rounding mode to use (defaults to HALF_UP)
 * @returns The rounded number with 2 decimal places
 * 
 * @example
 * ```typescript
 * round2(294.305, RoundingMode.HALF_UP)  // 294.31
 * round2(294.295, RoundingMode.HALF_UP)  // 294.30
 * round2(2.5, RoundingMode.BANKERS)      // 2
 * round2(3.5, RoundingMode.BANKERS)      // 4
 * ```
 */
export function round2(
  value: number, 
  mode: RoundingMode = RoundingMode.HALF_UP
): number {
  if (!isFinite(value)) {
    return value; // Return NaN, Infinity, or -Infinity as-is
  }

  const factor = 100;
  const isNegative = value < 0;
  const absValue = Math.abs(value);
  
  // Add small epsilon to handle floating point precision issues
  const scaled = (absValue + Number.EPSILON) * factor;

  if (mode === RoundingMode.BANKERS) {
    // Banker's Rounding: .5 rounds to nearest even number
    const floored = Math.floor(scaled);
    const fraction = scaled - floored;
    
    let result;
    // Check if we're very close to 0.5
    if (Math.abs(fraction - 0.5) < 0.0000001) {
      // Exactly 0.5: round to even
      result = (floored % 2 === 0 ? floored : floored + 1) / factor;
    } else {
      // Not exactly 0.5: use standard rounding
      result = Math.round(scaled) / factor;
    }
    
    return isNegative ? -result : result;
  }
  
  // HALF_UP: Standard commercial rounding
  const result = Math.round(scaled) / factor;
  return isNegative ? -result : result;
}

/**
 * Convenience function for standard commercial rounding to 2 decimal places
 * 
 * Uses HALF_UP rounding mode (0.5 always rounds up)
 * 
 * @param value - The number to round
 * @returns The rounded number with 2 decimal places
 * 
 * @example
 * ```typescript
 * round(294.305)  // 294.31
 * round(294.295)  // 294.30
 * round(2.5)      // 2.5
 * round(2.55)     // 2.55
 * round(2.555)    // 2.56
 * ```
 */
export const round = (value: number): number => round2(value, RoundingMode.HALF_UP);