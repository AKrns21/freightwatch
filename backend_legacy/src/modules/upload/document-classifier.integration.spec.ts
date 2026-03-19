/**
 * Integration test — DocumentClassifierService (Issue #22)
 *
 * Tests the full classify() pipeline against realistic file samples,
 * including an ambiguous PDF where only the LLM can determine the type.
 * The LLM test is skipped when ANTHROPIC_API_KEY is not set.
 */
import { Test, TestingModule } from '@nestjs/testing';
import { DocumentClassifierService } from './document-classifier.service';
import { DocType } from './entities/upload.entity';

// Minimal text content samples
const TARIFF_PDF_CONTENT = `
Carrier: DHL Express
Gültig ab: 01.01.2025

Gewichtszone   Zone 1   Zone 2   Zone 3
0 - 1 kg        5.90     7.20     8.50
1 - 5 kg        8.40    10.30    12.90
5 - 10 kg      12.50    15.80    19.20
> 10 kg         0.95     1.20     1.45  (pro kg)

Kraftstoffzuschlag: 12.5 %
Mautanteil:          4.8 %
`.trim();

const INVOICE_PDF_CONTENT = `
Rechnung Nr.: INV-2025-0042
Rechnungsdatum: 15.03.2025
Lieferant: DHL Express GmbH

Pos.  Beschreibung                        Menge  Einzelpreis  Gesamt
1     Standardpaket DE-DE (0-5kg)           42       4.90     205.80
2     Expresspaket DE-AT                     8       9.50      76.00
3     Kraftstoffzuschlag 12.5%              —        —         35.22

Nettobetrag:    317.02 EUR
MwSt. 19%:       60.23 EUR
Gesamtbetrag:   377.25 EUR
`.trim();

const SHIPMENT_CSV_CONTENT = `
date,reference,origin_zip,dest_zip,carrier,weight_kg,cost
2025-03-01,REF001,10115,80331,DHL,2.5,8.40
2025-03-01,REF002,20097,50667,UPS,12.0,18.90
2025-03-02,REF003,30159,70173,DPD,0.8,5.90
2025-03-02,REF004,40210,90402,Hermes,5.3,11.20
`.trim();

const AMBIGUOUS_PDF_CONTENT = `
Frachtdaten Export März 2025

Abrechnungsperiode: 01.03.2025 - 31.03.2025
Erstellt am: 01.04.2025

Sendungsnummer    PLZ Von    PLZ An    Gewicht    Betrag
S-2025-001        10115      80331      2.50 kg    8.40 €
S-2025-002        20097      50667     12.00 kg   18.90 €
S-2025-003        30159      70173      0.80 kg    5.90 €
`.trim();

describe('DocumentClassifierService — integration', () => {
  let service: DocumentClassifierService;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [DocumentClassifierService],
    }).compile();

    service = module.get<DocumentClassifierService>(DocumentClassifierService);
  });

  // ── Structured files: classify by extension + filename heuristics ────────────

  it('classifies CSV tariff file by filename', async () => {
    const result = await service.classify('Tarif_DHL_2025.csv', 'text/csv', TARIFF_PDF_CONTENT);
    expect(result).toBe(DocType.TARIFF);
  });

  it('classifies XLSX invoice file by filename', async () => {
    const result = await service.classify(
      'Rechnung_2025_042.xlsx',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      INVOICE_PDF_CONTENT
    );
    expect(result).toBe(DocType.INVOICE);
  });

  it('classifies CSV shipment export (no keyword match) → shipment_csv fallback', async () => {
    const result = await service.classify('export_march_2025.csv', 'text/csv', SHIPMENT_CSV_CONTENT);
    expect(result).toBe(DocType.SHIPMENT_CSV);
  });

  // ── PDFs: filename heuristics take priority over LLM ────────────────────────

  it('classifies PDF with tariff filename → tariff (no LLM needed)', async () => {
    const result = await service.classify('Entgelte_2025.pdf', 'application/pdf', TARIFF_PDF_CONTENT);
    expect(result).toBe(DocType.TARIFF);
  });

  it('classifies PDF with invoice filename → invoice (no LLM needed)', async () => {
    const result = await service.classify('Rechnung_April.pdf', 'application/pdf', INVOICE_PDF_CONTENT);
    expect(result).toBe(DocType.INVOICE);
  });

  // ── Ambiguous PDF: LLM fallback (skipped without API key) ───────────────────

  const llmTest = process.env.ANTHROPIC_API_KEY ? it : it.skip;

  llmTest(
    'classifies ambiguous PDF with neutral filename via LLM → shipment_csv',
    async () => {
      const result = await service.classify(
        'frachtdaten_export.pdf',
        'application/pdf',
        AMBIGUOUS_PDF_CONTENT
      );
      // LLM should recognise this as a list of shipments
      expect(result).toBe(DocType.SHIPMENT_CSV);
    },
    15_000 // allow up to 15s for API call
  );

  llmTest(
    'classifies ambiguous tariff PDF via LLM → tariff',
    async () => {
      const result = await service.classify(
        'carrier_pricing.pdf',
        'application/pdf',
        TARIFF_PDF_CONTENT
      );
      expect(result).toBe(DocType.TARIFF);
    },
    15_000
  );

  llmTest(
    'classifies ambiguous invoice PDF via LLM → invoice',
    async () => {
      const result = await service.classify(
        'carrier_document.pdf',
        'application/pdf',
        INVOICE_PDF_CONTENT
      );
      expect(result).toBe(DocType.INVOICE);
    },
    15_000
  );

  // ── User override mapping ────────────────────────────────────────────────────

  it('sourceType override: invoice → invoice', () => {
    expect(service.sourceTypeToDocType('invoice')).toBe(DocType.INVOICE);
  });

  it('sourceType override: rate_card → tariff', () => {
    expect(service.sourceTypeToDocType('rate_card')).toBe(DocType.TARIFF);
  });

  it('sourceType override: fleet_log → shipment_csv', () => {
    expect(service.sourceTypeToDocType('fleet_log')).toBe(DocType.SHIPMENT_CSV);
  });
});
