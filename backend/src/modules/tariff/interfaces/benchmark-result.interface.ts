export interface BenchmarkResult {
  expected_base_amount: number;
  expected_diesel_amount?: number;
  expected_toll_amount?: number;
  expected_total_amount: number;
  
  cost_breakdown: CostBreakdownItem[];
  
  calculation_metadata: {
    tariff_table_id: string;
    lane_type: string;
    zone_calculated: number;
    fx_rate_used?: number;
    fx_rate_date?: Date;
    calc_version: string;
  };
}

export interface CostBreakdownItem {
  item: string;
  description?: string;
  zone?: number;
  weight?: number;
  rate?: number;
  base?: number;
  pct?: number;
  value?: number;
  amount: number;
  currency: string;
  note?: string;
}