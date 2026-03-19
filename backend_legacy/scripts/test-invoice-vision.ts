/**
 * Standalone test script for invoice Vision extraction.
 *
 * Usage (from backend/):
 *   npx ts-node -e "require('dotenv').config()" scripts/test-invoice-vision.ts <pdf-path> [output-path]
 *
 * Or directly:
 *   npx ts-node scripts/test-invoice-vision.ts <pdf-path>
 */
import * as fs from 'fs';
import * as path from 'path';
import Anthropic from '@anthropic-ai/sdk';
import * as dotenv from 'dotenv';

dotenv.config({ path: path.resolve(__dirname, '../.env') });

// mupdf uses top-level await (ESM) – must be loaded via dynamic import in CommonJS
type MupdfModule = typeof import('mupdf');
async function getMupdf(): Promise<MupdfModule> {
  return (await import('mupdf')) as MupdfModule;
}

const RENDER_SCALE = 2;
const MIN_AVG_CHARS_PER_PAGE = 50;

async function extractPages(pdfPath: string) {
  const mupdf = await getMupdf();
  const buffer = fs.readFileSync(pdfPath);
  const doc = mupdf.Document.openDocument(new Uint8Array(buffer), 'application/pdf');
  const pageCount = doc.countPages();

  // Try text first
  const textParts: string[] = [];
  let totalChars = 0;
  for (let i = 0; i < pageCount; i++) {
    const text = doc.loadPage(i).toStructuredText('preserve-whitespace').asText();
    textParts.push(text);
    totalChars += text.trim().length;
  }
  const avgChars = totalChars / pageCount;
  const emptyPageCount = textParts.filter((t) => t.trim().length < 20).length;
  const emptyFraction = emptyPageCount / pageCount;

  console.log(`PDF: ${pageCount} pages, avg ${Math.round(avgChars)} chars/page`);
  console.log(`     Empty pages: ${emptyPageCount}/${pageCount} (${Math.round(emptyFraction * 100)}%)`);

  // Use text mode only if avg is sufficient AND most pages have real content.
  // Mixed PDFs (ERP cover pages + scanned invoices) have high avg but mostly empty pages.
  if (avgChars >= MIN_AVG_CHARS_PER_PAGE && emptyFraction < 0.5) {
    console.log('Mode: TEXT (selectable PDF)');
    return { mode: 'text' as const, text: textParts.join('\n\n---\n\n'), pageCount };
  }

  if (emptyFraction >= 0.5) {
    console.log(`Mode: VISION (mixed PDF – ${Math.round(emptyFraction * 100)}% empty pages, rendering all)`);
  }

  // Render pages as images
  console.log('Mode: VISION (scanned PDF) – rendering pages...');
  const pages: { page_number: number; image_base64: string; size_kb: number }[] = [];

  for (let i = 0; i < pageCount; i++) {
    const page = doc.loadPage(i);
    const matrix: [number, number, number, number, number, number] = [
      RENDER_SCALE, 0, 0, RENDER_SCALE, 0, 0,
    ];
    const pixmap = page.toPixmap(matrix, mupdf.ColorSpace.DeviceRGB, false, true);
    const pngBytes = pixmap.asPNG();
    const sizeKb = Math.round(pngBytes.length / 1024);
    pages.push({
      page_number: i,
      image_base64: Buffer.from(pngBytes).toString('base64'),
      size_kb: sizeKb,
    });
    console.log(`  Page ${i + 1}/${pageCount}: ${pixmap.getWidth()}x${pixmap.getHeight()} px, ${sizeKb} KB`);
  }

  return { mode: 'vision' as const, pages, pageCount };
}

function buildPrompt(pageCount: number): string {
  return `You are analyzing ${pageCount} page(s) of a scanned German freight carrier invoice (Frachtrechnung).

Extract all data and return ONLY a JSON object – no markdown, no explanation, no code fences.

The PDF may contain multiple separate invoices stapled together. Include each as a separate entry in the "invoices" array.

Required JSON structure:
{
  "invoices": [
    {
      "invoice_number": "string",
      "invoice_date": "YYYY-MM-DD",
      "carrier_name": "string",
      "customer_name": "string or null",
      "customer_number": "string or null",
      "total_net_amount": number or null,
      "total_gross_amount": number or null,
      "currency": "EUR",
      "lines": [
        {
          "shipment_date": "YYYY-MM-DD or null",
          "shipment_reference": "Auftragsnummer string or null",
          "tour": "Tour number string or null",
          "origin_zip": "5-digit PLZ extracted from Ladestelle address, e.g. 42551",
          "origin_country": "2-letter ISO code, default DE",
          "dest_zip": "5-digit PLZ extracted from Entladestelle address",
          "dest_country": "2-letter ISO code, default DE",
          "weight_kg": number or null,
          "unit_price": number or null,
          "line_total": number or null,
          "billing_type": "LA code, e.g. 200 or 201"
        }
      ]
    }
  ],
  "confidence": 0.0 to 1.0,
  "issues": ["list any data quality problems found"]
}

Rules:
- Dates: convert German format (dd.mm.yy or dd.mm.yyyy) to YYYY-MM-DD
- Numbers: remove thousand separators, use period as decimal separator
- PLZ extraction: from addresses like "D-42551 Velbert" extract "42551"
- If a field is not visible or illegible, use null
- Each line item corresponds to one shipment row (LA code + Menge + Preis + GesamtEUR row)
- Ignore cover sheets, booking stamps (GEBUCHT/BEZAHLT), and VAT summary rows`;
}

async function main() {
  const pdfPath = process.argv[2];
  const outputPath = process.argv[3] ?? path.resolve(__dirname, '../../data/invoice-extraction.json');

  if (!pdfPath) {
    console.error('Usage: ts-node scripts/test-invoice-vision.ts <pdf-path> [output-path]');
    process.exit(1);
  }

  if (!process.env.ANTHROPIC_API_KEY) {
    console.error('ANTHROPIC_API_KEY not set in .env');
    process.exit(1);
  }

  console.log(`\nProcessing: ${path.basename(pdfPath)}`);
  console.log('─'.repeat(60));

  const extraction = await extractPages(pdfPath);

  if (extraction.mode === 'text') {
    console.log('\nText extraction result (first 500 chars):');
    console.log(extraction.text!.slice(0, 500));
    fs.writeFileSync(outputPath, JSON.stringify({ mode: 'text', text: extraction.text }, null, 2));
    console.log(`\nSaved to: ${outputPath}`);
    return;
  }

  // Vision mode – call Claude
  const pages = extraction.pages!;
  const totalKb = pages.reduce((sum, p) => sum + p.size_kb, 0);
  console.log(`\nSending ${pages.length} pages (${totalKb} KB total) to Claude Vision...`);

  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const imageBlocks: Anthropic.ImageBlockParam[] = pages.map((page) => ({
    type: 'image',
    source: {
      type: 'base64',
      media_type: 'image/png',
      data: page.image_base64,
    },
  }));

  const t0 = Date.now();
  const response = await client.messages.create({
    model: 'claude-sonnet-4-6',
    max_tokens: 16000,
    messages: [
      {
        role: 'user',
        content: [
          ...imageBlocks,
          { type: 'text', text: buildPrompt(pages.length) },
        ],
      },
    ],
  });
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

  const raw = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === 'text')
    .map((b) => b.text)
    .join('');

  console.log(`\nClaude responded in ${elapsed}s (${raw.length} chars)`);

  // Parse + pretty-print
  let parsed: unknown;
  try {
    const cleaned = raw.replace(/^```(?:json)?\n?/m, '').replace(/\n?```$/m, '').trim();
    parsed = JSON.parse(cleaned);
  } catch {
    console.error('\nFailed to parse JSON response. Raw output:');
    console.log(raw);
    fs.writeFileSync(outputPath.replace('.json', '-raw.txt'), raw);
    process.exit(1);
  }

  const output = JSON.stringify(parsed, null, 2);
  fs.writeFileSync(outputPath, output);

  // Print summary
  const data = parsed as { invoices?: Array<{ invoice_number?: string; lines?: unknown[] }>; confidence?: number };
  console.log('\n─'.repeat(60));
  console.log(`Invoices found: ${data.invoices?.length ?? 0}`);
  data.invoices?.forEach((inv, i) => {
    console.log(`  [${i + 1}] ${inv.invoice_number} – ${inv.lines?.length ?? 0} lines`);
  });
  console.log(`Confidence: ${data.confidence ?? '?'}`);
  console.log(`\nSaved to: ${outputPath}`);
}

main().catch((err) => {
  console.error('Error:', err);
  process.exit(1);
});
