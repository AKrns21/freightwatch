import { Test, TestingModule } from '@nestjs/testing';
import { DocumentClassifierService } from './document-classifier.service';
import { DocType } from './entities/upload.entity';

describe('DocumentClassifierService — heuristic rules', () => {
  let service: DocumentClassifierService;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [DocumentClassifierService],
    }).compile();

    service = module.get<DocumentClassifierService>(DocumentClassifierService);
  });

  // ── isStructuredFile ────────────────────────────────────────────────────────

  describe('isStructuredFile', () => {
    it('detects .csv extension', () => {
      expect(service.isStructuredFile('shipments.csv', 'application/octet-stream')).toBe(true);
    });

    it('detects .xlsx extension', () => {
      expect(service.isStructuredFile('preisliste.xlsx', 'application/octet-stream')).toBe(true);
    });

    it('detects .xls extension', () => {
      expect(service.isStructuredFile('data.xls', 'application/octet-stream')).toBe(true);
    });

    it('detects CSV MIME type', () => {
      expect(service.isStructuredFile('unknown', 'text/csv')).toBe(true);
    });

    it('detects Excel MIME type (.xlsx)', () => {
      expect(
        service.isStructuredFile(
          'unknown',
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
      ).toBe(true);
    });

    it('detects Excel MIME type (.xls)', () => {
      expect(service.isStructuredFile('unknown', 'application/vnd.ms-excel')).toBe(true);
    });

    it('returns false for PDF', () => {
      expect(service.isStructuredFile('invoice.pdf', 'application/pdf')).toBe(false);
    });

    it('returns false for image', () => {
      expect(service.isStructuredFile('scan.jpg', 'image/jpeg')).toBe(false);
    });
  });

  // ── classifyByFilename — tariff keywords ────────────────────────────────────

  describe('classifyByFilename — tariff', () => {
    const tariffCases = [
      'Tarif_DHL_2024.xlsx',
      'Entgelte_Q1.pdf',
      'Preisliste_Carrier.xlsx',
      'preistabelle_inbound.csv',
      'Frachttabelle_DE.xlsx',
      'frachtsatz_2025.csv',
      'rate_card_UPS.xlsx',
      'ratecard_FedEx.pdf',
      'Ratenkarte_2024.xlsx',
    ];

    for (const filename of tariffCases) {
      it(`detects TARIFF: "${filename}"`, () => {
        expect(service.classifyByFilename(filename)).toBe(DocType.TARIFF);
      });
    }
  });

  // ── classifyByFilename — invoice keywords ───────────────────────────────────

  describe('classifyByFilename — invoice', () => {
    const invoiceCases = [
      'Rechnung_2024-001.pdf',
      'rechnung_april.pdf',
      'Invoice_DHL_March.pdf',
      'Faktura_001.pdf',
      'Gutschrift_002.pdf',
      'RG 2024-05.pdf',
      'carrier_rg_april.pdf',
      'april-rg-2024.csv',
    ];

    for (const filename of invoiceCases) {
      it(`detects INVOICE: "${filename}"`, () => {
        expect(service.classifyByFilename(filename)).toBe(DocType.INVOICE);
      });
    }
  });

  // ── classifyByFilename — no match ───────────────────────────────────────────

  describe('classifyByFilename — no match', () => {
    const noMatchCases = [
      'sendungen_2024_april.csv',
      'shipment_export.xlsx',
      'daten.pdf',
      'report.pdf',
      'hergang.txt',          // contains "rg" inside a word — must NOT match
      'programmiert.csv',     // contains "rg" inside a word — must NOT match
    ];

    for (const filename of noMatchCases) {
      it(`returns null for "${filename}"`, () => {
        expect(service.classifyByFilename(filename)).toBeNull();
      });
    }
  });

  // ── sourceTypeToDocType ──────────────────────────────────────────────────────

  describe('sourceTypeToDocType', () => {
    it('maps invoice → invoice', () => {
      expect(service.sourceTypeToDocType('invoice')).toBe(DocType.INVOICE);
    });

    it('maps rate_card → tariff', () => {
      expect(service.sourceTypeToDocType('rate_card')).toBe(DocType.TARIFF);
    });

    it('maps fleet_log → shipment_csv', () => {
      expect(service.sourceTypeToDocType('fleet_log')).toBe(DocType.SHIPMENT_CSV);
    });

    it('maps unknown values → other', () => {
      expect(service.sourceTypeToDocType('something_else')).toBe(DocType.OTHER);
    });
  });

  // ── classify — sync paths (no LLM) ──────────────────────────────────────────

  describe('classify — deterministic paths', () => {
    it('classifies CSV with tariff filename → tariff', async () => {
      const result = await service.classify('Tarif_DHL_2024.csv', 'text/csv');
      expect(result).toBe(DocType.TARIFF);
    });

    it('classifies XLSX with invoice filename → invoice', async () => {
      const result = await service.classify(
        'Rechnung_001.xlsx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
      );
      expect(result).toBe(DocType.INVOICE);
    });

    it('classifies CSV with no matching keyword → shipment_csv (structured fallback)', async () => {
      const result = await service.classify('export_april.csv', 'text/csv');
      expect(result).toBe(DocType.SHIPMENT_CSV);
    });

    it('classifies PDF with no keyword and no LLM → other', async () => {
      // LLM unavailable (no ANTHROPIC_API_KEY in test env)
      const result = await service.classify('random.pdf', 'application/pdf');
      expect(result).toBe(DocType.OTHER);
    });

    it('classifies PDF with tariff keyword → tariff (no LLM needed)', async () => {
      const result = await service.classify('Preisliste_UPS.pdf', 'application/pdf');
      expect(result).toBe(DocType.TARIFF);
    });

    it('classifies PDF with invoice keyword → invoice (no LLM needed)', async () => {
      const result = await service.classify('Rechnung_April.pdf', 'application/pdf');
      expect(result).toBe(DocType.INVOICE);
    });
  });
});
