import { round, round2, RoundingMode } from '../../src/utils/round';

describe('Rounding Utilities', () => {
  describe('round2 function', () => {
    describe('HALF_UP mode (default)', () => {
      it('should round 0.5 up', () => {
        expect(round2(2.5)).toBe(2.5);
        expect(round2(2.55)).toBe(2.55);
        expect(round2(2.555)).toBe(2.56);
        expect(round2(2.505)).toBe(2.51);
      });

      it('should handle the specified test cases', () => {
        expect(round2(294.305)).toBe(294.31);
        expect(round2(294.295)).toBe(294.30);
      });

      it('should round negative numbers correctly', () => {
        expect(round2(-2.555)).toBe(-2.56);
        expect(round2(-2.554)).toBe(-2.55);
        expect(round2(-294.305)).toBe(-294.31);
        expect(round2(-294.295)).toBe(-294.30);
      });

      it('should handle edge cases', () => {
        expect(round2(0)).toBe(0);
        expect(round2(0.1)).toBe(0.1);
        expect(round2(0.01)).toBe(0.01);
        expect(round2(0.001)).toBe(0.0);
        expect(round2(0.005)).toBe(0.01);
        expect(round2(0.004)).toBe(0.0);
      });

      it('should handle large numbers', () => {
        expect(round2(999999.995)).toBe(1000000.0);
        expect(round2(1234567.895)).toBe(1234567.90);
        expect(round2(1234567.894)).toBe(1234567.89);
      });

      it('should handle very small numbers', () => {
        expect(round2(0.0001)).toBe(0.0);
        expect(round2(0.0051)).toBe(0.01);
        expect(round2(0.0049)).toBe(0.0);
      });
    });

    describe('BANKERS mode', () => {
      it('should round 0.5 to nearest even number', () => {
        expect(round2(2.505, RoundingMode.BANKERS)).toBe(2.5);  // 250.5 -> 250 (even)
        expect(round2(3.505, RoundingMode.BANKERS)).toBe(3.5);  // 350.5 -> 350 (even)
        expect(round2(4.505, RoundingMode.BANKERS)).toBe(4.5);  // 450.5 -> 450 (even)
        expect(round2(5.505, RoundingMode.BANKERS)).toBe(5.5);  // 550.5 -> 550 (even)
      });

      it('should handle decimal cases with banker\'s rounding', () => {
        expect(round2(2.505, RoundingMode.BANKERS)).toBe(2.5); // 250.5 -> 250 (even)
        expect(round2(2.515, RoundingMode.BANKERS)).toBe(2.52); // 251.5 -> 252 (even)
        expect(round2(2.525, RoundingMode.BANKERS)).toBe(2.52); // 252.5 -> 252 (even)
        expect(round2(2.535, RoundingMode.BANKERS)).toBe(2.54); // 253.5 -> 254 (even)
      });

      it('should handle negative numbers with banker\'s rounding', () => {
        expect(round2(-2.505, RoundingMode.BANKERS)).toBe(-2.5);
        expect(round2(-3.505, RoundingMode.BANKERS)).toBe(-3.5);
        expect(round2(-2.515, RoundingMode.BANKERS)).toBe(-2.52);
        expect(round2(-2.525, RoundingMode.BANKERS)).toBe(-2.52);
      });

      it('should round non-0.5 values normally', () => {
        expect(round2(2.54, RoundingMode.BANKERS)).toBe(2.54);
        expect(round2(2.56, RoundingMode.BANKERS)).toBe(2.56);
        expect(round2(2.549, RoundingMode.BANKERS)).toBe(2.55);
        expect(round2(2.551, RoundingMode.BANKERS)).toBe(2.55);
      });

      it('should handle edge cases', () => {
        expect(round2(0.005, RoundingMode.BANKERS)).toBe(0.0);  // 0.5 -> 0 (even)
        expect(round2(0.015, RoundingMode.BANKERS)).toBe(0.02); // 1.5 -> 2 (even)
        expect(round2(0.025, RoundingMode.BANKERS)).toBe(0.02); // 2.5 -> 2 (even)
        expect(round2(0.035, RoundingMode.BANKERS)).toBe(0.04); // 3.5 -> 4 (even)
      });
    });

    describe('Special values', () => {
      it('should handle NaN', () => {
        expect(round2(NaN)).toBeNaN();
        expect(round2(NaN, RoundingMode.BANKERS)).toBeNaN();
      });

      it('should handle Infinity', () => {
        expect(round2(Infinity)).toBe(Infinity);
        expect(round2(-Infinity)).toBe(-Infinity);
        expect(round2(Infinity, RoundingMode.BANKERS)).toBe(Infinity);
        expect(round2(-Infinity, RoundingMode.BANKERS)).toBe(-Infinity);
      });
    });
  });

  describe('round convenience function', () => {
    it('should use HALF_UP mode by default', () => {
      expect(round(294.305)).toBe(294.31);
      expect(round(294.295)).toBe(294.30);
      expect(round(2.5)).toBe(2.5);
      expect(round(2.555)).toBe(2.56);
    });

    it('should be equivalent to round2 with HALF_UP', () => {
      const testValues = [
        0, 0.1, 0.01, 0.005, 0.004,
        1.234, 2.555, 3.445, 
        294.305, 294.295,
        -1.234, -2.555, -3.445,
        999.995, 1000000.001
      ];

      testValues.forEach(value => {
        expect(round(value)).toBe(round2(value, RoundingMode.HALF_UP));
      });
    });

    it('should handle special values', () => {
      expect(round(NaN)).toBeNaN();
      expect(round(Infinity)).toBe(Infinity);
      expect(round(-Infinity)).toBe(-Infinity);
    });
  });

  describe('Floating point precision handling', () => {
    it('should handle floating point precision issues', () => {
      // These test cases address common floating point precision issues
      expect(round2(0.1 + 0.2)).toBe(0.30); // 0.30000000000000004
      expect(round2(1.005)).toBe(1.01);     // Should round up despite floating point issues
      expect(round2(2.675)).toBe(2.68);     // Should round up despite floating point issues
    });

    it('should be consistent with repeated operations', () => {
      const value = 123.456789;
      const rounded1 = round2(value);
      const rounded2 = round2(rounded1);
      expect(rounded1).toBe(rounded2);
    });
  });

  describe('Financial calculation scenarios', () => {
    it('should handle typical freight cost calculations', () => {
      // Base cost calculation
      const baseCost = 294.30;
      const dieselPct = 18.5;
      const dieselAmount = round(baseCost * (dieselPct / 100));
      expect(dieselAmount).toBe(54.45);

      // Total calculation
      const toll = 15.20;
      const total = round(baseCost + dieselAmount + toll);
      expect(total).toBe(363.95);
    });

    it('should handle currency conversion scenarios', () => {
      const eurAmount = 1000.00;
      const exchangeRate = 1.08567;
      const usdAmount = round(eurAmount * exchangeRate);
      expect(usdAmount).toBe(1085.67);
    });

    it('should maintain precision in chained calculations', () => {
      let amount = 1000.00;
      amount = round(amount * 1.19); // Add 19% VAT
      expect(amount).toBe(1190.00);
      
      amount = round(amount * 0.85); // Apply 15% discount
      expect(amount).toBe(1011.50);
      
      amount = round(amount + 25.337); // Add surcharge
      expect(amount).toBe(1036.84);
    });
  });

  describe('Performance and consistency', () => {
    it('should be deterministic with same inputs', () => {
      const testValue = 123.456789;
      const results = Array(100).fill(0).map(() => round2(testValue));
      const firstResult = results[0];
      
      expect(results.every(result => result === firstResult)).toBe(true);
    });

    it('should handle arrays of values consistently', () => {
      const values = [1.235, 2.345, 3.455, 4.565, 5.675];
      const rounded = values.map(round);
      
      expect(rounded).toEqual([1.24, 2.35, 3.46, 4.57, 5.68]);
    });
  });
});