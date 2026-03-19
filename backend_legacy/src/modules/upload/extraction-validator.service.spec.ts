import { Test, TestingModule } from '@nestjs/testing';
import { getRepositoryToken } from '@nestjs/typeorm';
import { Shipment } from '@/modules/parsing/entities/shipment.entity';
import {
  ExtractionValidatorService,
  InvoiceHeaderInput,
  InvoiceLineInput,
  ShipmentInput,
  TariffRateInput,
  TariffZoneMapInput,
} from './extraction-validator.service';

const TENANT_ID = 'tenant-abc';

function makeLine(overrides: Partial<InvoiceLineInput> = {}): InvoiceLineInput {
  return { index: 1, line_total: 100, weight_kg: 10, dest_zip: '10115', dest_country: 'DE', ...overrides };
}

describe('ExtractionValidatorService', () => {
  let service: ExtractionValidatorService;
  let shipmentRepo: { find: jest.Mock };

  beforeEach(async () => {
    shipmentRepo = { find: jest.fn().mockResolvedValue([]) };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        ExtractionValidatorService,
        {
          provide: getRepositoryToken(Shipment),
          useValue: shipmentRepo,
        },
      ],
    }).compile();

    service = module.get<ExtractionValidatorService>(ExtractionValidatorService);
  });

  afterEach(() => jest.clearAllMocks());

  // ---------------------------------------------------------------------------
  // Rule 1 — invoice total reconciliation ±2%
  // ---------------------------------------------------------------------------
  describe('validateInvoice — Rule 1: total reconciliation', () => {
    it('passes when line sum equals header total exactly', () => {
      const header: InvoiceHeaderInput = { total_net: 200 };
      const lines = [makeLine({ index: 1, line_total: 120 }), makeLine({ index: 2, line_total: 80 })];

      const result = service.validateInvoice(header, lines);

      expect(result.status).toBe('pass');
      expect(result.violations).toHaveLength(0);
    });

    it('passes when difference is within 2% tolerance', () => {
      const header: InvoiceHeaderInput = { total_net: 1000 };
      // 1000 × 2% = 20 EUR tolerance; diff = 19 EUR → pass
      const lines = [makeLine({ index: 1, line_total: 981 })];

      const result = service.validateInvoice(header, lines);

      expect(result.status).toBe('pass');
    });

    it('returns hold_for_review when difference exceeds 2% tolerance', () => {
      const header: InvoiceHeaderInput = { total_net: 1000 };
      // diff = 30 > 20 tolerance
      const lines = [makeLine({ index: 1, line_total: 970 })];

      const result = service.validateInvoice(header, lines);

      expect(result.status).toBe('review');
      expect(result.violations).toHaveLength(1);
      expect(result.violations[0].rule).toBe('invoice_total_reconciliation');
      expect(result.violations[0].action).toBe('hold_for_review');
    });

    it('skips total check when header.total_net is null', () => {
      const header: InvoiceHeaderInput = { total_net: null };
      const lines = [makeLine({ index: 1, line_total: 100 })];

      const result = service.validateInvoice(header, lines);

      expect(result.violations.filter((v) => v.rule === 'invoice_total_reconciliation')).toHaveLength(0);
    });
  });

  // ---------------------------------------------------------------------------
  // Rule 2 — weight_kg > 0
  // ---------------------------------------------------------------------------
  describe('validateInvoice — Rule 2: weight_kg positive', () => {
    it('passes when weight is positive', () => {
      const result = service.validateInvoice({ total_net: null }, [makeLine({ weight_kg: 0.5 })]);
      expect(result.status).toBe('pass');
    });

    it('rejects line when weight_kg is 0', () => {
      const result = service.validateInvoice({ total_net: null }, [makeLine({ index: 3, weight_kg: 0 })]);

      expect(result.status).toBe('fail');
      expect(result.violations[0].rule).toBe('weight_positive');
      expect(result.violations[0].action).toBe('reject');
      expect(result.violations[0].index).toBe(3);
    });

    it('rejects line when weight_kg is negative', () => {
      const result = service.validateInvoice({ total_net: null }, [makeLine({ weight_kg: -5 })]);

      expect(result.status).toBe('fail');
      expect(result.violations[0].rule).toBe('weight_positive');
    });

    it('skips weight check when weight_kg is null', () => {
      const result = service.validateInvoice({ total_net: null }, [makeLine({ weight_kg: null })]);
      expect(result.violations.filter((v) => v.rule === 'weight_positive')).toHaveLength(0);
    });
  });

  // ---------------------------------------------------------------------------
  // Rule 3 — dest_zip format (DE only)
  // ---------------------------------------------------------------------------
  describe('validateInvoice — Rule 3: dest_zip format', () => {
    it('passes for valid 5-digit German zip', () => {
      const result = service.validateInvoice({ total_net: null }, [makeLine({ dest_zip: '80331' })]);
      expect(result.violations.filter((v) => v.rule === 'dest_zip_format_de')).toHaveLength(0);
    });

    it('warns for zip with letters', () => {
      const result = service.validateInvoice({ total_net: null }, [
        makeLine({ dest_zip: 'ABCDE', dest_country: 'DE' }),
      ]);

      expect(result.status).toBe('pass'); // warn does not escalate to fail/review
      expect(result.violations[0].rule).toBe('dest_zip_format_de');
      expect(result.violations[0].action).toBe('warn');
    });

    it('warns for zip shorter than 5 digits', () => {
      const result = service.validateInvoice({ total_net: null }, [makeLine({ dest_zip: '1011' })]);
      expect(result.violations[0].rule).toBe('dest_zip_format_de');
    });

    it('skips zip check for non-DE destination', () => {
      const result = service.validateInvoice({ total_net: null }, [
        makeLine({ dest_zip: 'W1A 1AA', dest_country: 'GB' }),
      ]);
      expect(result.violations.filter((v) => v.rule === 'dest_zip_format_de')).toHaveLength(0);
    });

    it('applies zip check when dest_country is absent (defaults to DE)', () => {
      const result = service.validateInvoice({ total_net: null }, [
        makeLine({ dest_zip: 'bad', dest_country: null }),
      ]);
      expect(result.violations[0].rule).toBe('dest_zip_format_de');
    });
  });

  // ---------------------------------------------------------------------------
  // Rule 4 — shipment reference deduplication
  // ---------------------------------------------------------------------------
  describe('validateShipments — Rule 4: reference_number dedup', () => {
    it('passes when no references exist in DB', async () => {
      shipmentRepo.find.mockResolvedValue([]);
      const shipments: ShipmentInput[] = [{ index: 0, reference_number: 'REF-001' }];

      const result = await service.validateShipments(shipments, TENANT_ID);

      expect(result.status).toBe('pass');
    });

    it('rejects shipment whose reference_number already exists for tenant', async () => {
      shipmentRepo.find.mockResolvedValue([{ reference_number: 'REF-001' }]);
      const shipments: ShipmentInput[] = [{ index: 0, reference_number: 'REF-001' }];

      const result = await service.validateShipments(shipments, TENANT_ID);

      expect(result.status).toBe('fail');
      expect(result.violations[0].rule).toBe('reference_number_dedup');
      expect(result.violations[0].action).toBe('reject');
    });

    it('only rejects duplicate rows, passes unique ones', async () => {
      shipmentRepo.find.mockResolvedValue([{ reference_number: 'REF-001' }]);
      const shipments: ShipmentInput[] = [
        { index: 0, reference_number: 'REF-001' }, // duplicate
        { index: 1, reference_number: 'REF-002' }, // new
      ];

      const result = await service.validateShipments(shipments, TENANT_ID);

      expect(result.violations).toHaveLength(1);
      expect(result.violations[0].index).toBe(0);
    });

    it('skips DB query when all reference_numbers are null/empty', async () => {
      const shipments: ShipmentInput[] = [{ index: 0, reference_number: null }];

      const result = await service.validateShipments(shipments, TENANT_ID);

      expect(shipmentRepo.find).not.toHaveBeenCalled();
      expect(result.status).toBe('pass');
    });
  });

  // ---------------------------------------------------------------------------
  // Rule 5 — tariff rate weight band integrity
  // ---------------------------------------------------------------------------
  describe('validateTariffRates — Rule 5: weight_from < weight_to', () => {
    it('passes when weight_from_kg < weight_to_kg', () => {
      const rates: TariffRateInput[] = [{ index: 0, weight_from_kg: 0, weight_to_kg: 5 }];
      const result = service.validateTariffRates(rates);
      expect(result.status).toBe('pass');
    });

    it('rejects when weight_from_kg equals weight_to_kg', () => {
      const rates: TariffRateInput[] = [{ index: 0, weight_from_kg: 10, weight_to_kg: 10 }];
      const result = service.validateTariffRates(rates);

      expect(result.status).toBe('fail');
      expect(result.violations[0].rule).toBe('weight_band_integrity');
      expect(result.violations[0].action).toBe('reject');
    });

    it('rejects when weight_from_kg > weight_to_kg', () => {
      const rates: TariffRateInput[] = [{ index: 2, weight_from_kg: 20, weight_to_kg: 5 }];
      const result = service.validateTariffRates(rates);

      expect(result.status).toBe('fail');
      expect(result.violations[0].index).toBe(2);
    });

    it('reports all invalid bands in a multi-row table', () => {
      const rates: TariffRateInput[] = [
        { index: 0, weight_from_kg: 0, weight_to_kg: 5 },   // ok
        { index: 1, weight_from_kg: 10, weight_to_kg: 5 },  // bad
        { index: 2, weight_from_kg: 5, weight_to_kg: 20 },  // ok
        { index: 3, weight_from_kg: 20, weight_to_kg: 20 }, // bad
      ];

      const result = service.validateTariffRates(rates);

      expect(result.violations).toHaveLength(2);
      expect(result.violations.map((v) => v.index)).toEqual([1, 3]);
    });
  });

  // ---------------------------------------------------------------------------
  // Rule 6 — tariff zone map PLZ prefix validity
  // ---------------------------------------------------------------------------
  describe('validateTariffZoneMap — Rule 6: plz_prefix valid', () => {
    it('passes for valid 2-digit prefix', () => {
      const entries: TariffZoneMapInput[] = [{ index: 0, plz_prefix: '10' }];
      const result = service.validateTariffZoneMap(entries);
      expect(result.status).toBe('pass');
    });

    it('passes for valid 5-digit prefix (full zip)', () => {
      const entries: TariffZoneMapInput[] = [{ index: 0, plz_prefix: '99999' }];
      const result = service.validateTariffZoneMap(entries);
      expect(result.status).toBe('pass');
    });

    it('passes for single-digit prefix', () => {
      const entries: TariffZoneMapInput[] = [{ index: 0, plz_prefix: '1' }];
      const result = service.validateTariffZoneMap(entries);
      expect(result.status).toBe('pass');
    });

    it('rejects prefix with non-digit characters', () => {
      const entries: TariffZoneMapInput[] = [{ index: 0, plz_prefix: '10A' }];
      const result = service.validateTariffZoneMap(entries);

      expect(result.status).toBe('fail');
      expect(result.violations[0].rule).toBe('plz_prefix_valid');
      expect(result.violations[0].action).toBe('reject');
    });

    it('rejects empty string prefix', () => {
      const entries: TariffZoneMapInput[] = [{ index: 0, plz_prefix: '' }];
      const result = service.validateTariffZoneMap(entries);
      expect(result.status).toBe('fail');
    });

    it('rejects prefix longer than 5 digits', () => {
      const entries: TariffZoneMapInput[] = [{ index: 0, plz_prefix: '123456' }];
      const result = service.validateTariffZoneMap(entries);
      expect(result.status).toBe('fail');
    });
  });

  // ---------------------------------------------------------------------------
  // Status derivation
  // ---------------------------------------------------------------------------
  describe('status derivation', () => {
    it('returns fail when any violation is reject', () => {
      // weight = 0 → reject
      const result = service.validateInvoice({ total_net: null }, [makeLine({ weight_kg: 0 })]);
      expect(result.status).toBe('fail');
    });

    it('returns review when only hold_for_review violations', () => {
      // total mismatch > 2% → hold_for_review
      const result = service.validateInvoice({ total_net: 1000 }, [makeLine({ line_total: 500 })]);
      expect(result.status).toBe('review');
    });

    it('returns pass when only warn violations', () => {
      // bad zip → warn
      const result = service.validateInvoice({ total_net: null }, [makeLine({ dest_zip: 'bad' })]);
      expect(result.status).toBe('pass');
    });

    it('prefers fail over review when both present', () => {
      // total mismatch (review) + weight = 0 (fail)
      const result = service.validateInvoice({ total_net: 1000 }, [
        makeLine({ index: 1, line_total: 500, weight_kg: 0 }),
      ]);
      expect(result.status).toBe('fail');
    });
  });
});
