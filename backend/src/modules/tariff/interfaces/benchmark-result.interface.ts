export interface BenchmarkResult {
  expected_base_amount: number;
  expected_diesel_amount?: number;
  expected_toll_amount?: number;
  expected_total_amount: number;
  
  actual_total_amount?: number;
  delta_amount?: number;
  delta_pct?: number;
  classification?: string;
  
  cost_breakdown: CostBreakdownItem[];
  
  report_amounts?: {
    expected_base_amount: number;
    expected_diesel_amount?: number;
    expected_toll_amount?: number;
    expected_total_amount: number;
    actual_total_amount?: number;
    delta_amount?: number;
    currency: string;
  };
  
  calculation_metadata: {
    tariff_table_id: string;
    lane_type: string;
    zone_calculated: number;
    fx_rate_used?: number;
    fx_rate_date?: Date;
    diesel_basis_used?: string;
    diesel_pct_used?: number;
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