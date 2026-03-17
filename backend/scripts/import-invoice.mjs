/**
 * Invoice PDF import script
 *
 * Extracts invoice data from a PDF using Claude Vision,
 * then inserts invoice_header + invoice_line rows into Supabase
 * via the REST API (no postgres password required).
 *
 * Usage (from backend/):
 *   node scripts/import-invoice.mjs <pdf-path> [tenant-id]
 *
 * If no tenant-id is given, a demo tenant is created automatically.
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, basename, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createClient } from '@supabase/supabase-js';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ─── LOAD .env ────────────────────────────────────────────────────────────────
try {
  const envContent = readFileSync(resolve(__dirname, '../.env'), 'utf8');
  for (const line of envContent.split('\n')) {
    const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
} catch { /* .env optional */ }

const PDF_PATH  = process.argv[2];
const TENANT_ID = process.argv[3] ?? null;

if (!PDF_PATH) {
  console.error('Usage: node scripts/import-invoice.mjs <pdf-path> [tenant-id]');
  process.exit(1);
}
if (!process.env.ANTHROPIC_API_KEY) { console.error('ANTHROPIC_API_KEY missing'); process.exit(1); }
if (!process.env.SUPABASE_URL)      { console.error('SUPABASE_URL missing');      process.exit(1); }
if (!process.env.SUPABASE_SERVICE_ROLE_KEY) { console.error('SUPABASE_SERVICE_ROLE_KEY missing'); process.exit(1); }

const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);

// ─── RENDER PDF ───────────────────────────────────────────────────────────────
async function renderPages(pdfPath) {
  const mupdf = await import('mupdf');
  const buffer = readFileSync(pdfPath);
  const doc = mupdf.Document.openDocument(new Uint8Array(buffer), 'application/pdf');
  const pageCount = doc.countPages();

  console.log(`\nFile:  ${basename(pdfPath)}`);
  console.log(`Pages: ${pageCount}`);

  // Try text first
  const textParts = [];
  let totalChars = 0;
  for (let i = 0; i < pageCount; i++) {
    const text = doc.loadPage(i).toStructuredText('preserve-whitespace').asText();
    textParts.push(text);
    totalChars += text.trim().length;
  }
  const avgChars = totalChars / pageCount;
  const emptyFraction = textParts.filter(t => t.trim().length < 20).length / pageCount;

  console.log(`Avg chars/page: ${Math.round(avgChars)}, empty: ${Math.round(emptyFraction*100)}%`);

  if (avgChars >= 50 && emptyFraction < 0.5) {
    console.log('Mode: TEXT');
    return { mode: 'text', text: textParts.join('\n\n---PAGE---\n\n'), pageCount };
  }

  console.log('Mode: VISION – rendering pages...');
  const pages = [];
  for (let i = 0; i < pageCount; i++) {
    const page = doc.loadPage(i);
    const pixmap = page.toPixmap([2,0,0,2,0,0], mupdf.ColorSpace.DeviceRGB, false, true);
    const pngBytes = pixmap.asPNG();
    pages.push({ page_number: i, image_base64: Buffer.from(pngBytes).toString('base64'), size_kb: Math.round(pngBytes.length/1024) });
    console.log(`  Page ${i+1}/${pageCount}: ${pixmap.getWidth()}x${pixmap.getHeight()} px, ${Math.round(pngBytes.length/1024)} KB`);
  }
  return { mode: 'vision', pages, pageCount };
}

// ─── PROMPT ───────────────────────────────────────────────────────────────────
function buildPrompt(pageCount, mode, textContent) {
  const intro = mode === 'text'
    ? `You are analyzing text from a German freight carrier invoice.`
    : `You are analyzing ${pageCount} page(s) of a scanned German freight carrier invoice.`;
  const textSection = mode === 'text' ? `\n\nExtracted text:\n\`\`\`\n${textContent}\n\`\`\`` : '';

  return `${intro}${textSection}

Extract ALL data. Return ONLY valid JSON — no markdown, no explanations.

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
          "shipment_reference": "Auftragsnummer or null",
          "tour": "Tour-Nr or null",
          "origin_zip": "5-digit PLZ or null",
          "origin_country": "DE",
          "dest_zip": "5-digit PLZ or null",
          "dest_country": "DE",
          "weight_kg": number or null,
          "unit_price": number or null,
          "line_total": number or null,
          "billing_type": "LA code e.g. 200 or null"
        }
      ]
    }
  ],
  "confidence": 0.0,
  "issues": []
}

Rules:
- Dates: convert German format dd.mm.yyyy → YYYY-MM-DD
- Numbers: remove thousand separators, use period decimal separator
- PLZ: extract from addresses like "D-42551 Velbert" → "42551"
- If a field is missing/illegible use null`;
}

// ─── CALL CLAUDE ──────────────────────────────────────────────────────────────
async function callClaude(extraction) {
  const { default: Anthropic } = await import('@anthropic-ai/sdk');
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  let content;
  if (extraction.mode === 'text') {
    content = [{ type: 'text', text: buildPrompt(extraction.pageCount, 'text', extraction.text) }];
  } else {
    const totalKb = extraction.pages.reduce((s,p) => s+p.size_kb, 0);
    console.log(`\nSending ${extraction.pages.length} pages (${totalKb} KB) to Claude Vision...`);
    content = [
      ...extraction.pages.map(p => ({ type: 'image', source: { type: 'base64', media_type: 'image/png', data: p.image_base64 } })),
      { type: 'text', text: buildPrompt(extraction.pageCount, 'vision', null) }
    ];
  }

  const t0 = Date.now();
  let raw = '';
  const stream = await client.messages.stream({ model: 'claude-sonnet-4-6', max_tokens: 16000, messages: [{ role: 'user', content }] });
  for await (const event of stream) {
    if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') raw += event.delta.text;
  }
  console.log(`Claude responded in ${((Date.now()-t0)/1000).toFixed(1)}s`);
  return raw;
}

// ─── ENSURE TENANT ────────────────────────────────────────────────────────────
async function ensureTenant(tenantId) {
  if (tenantId) {
    const { data } = await supabase.from('tenant').select('id').eq('id', tenantId).single();
    if (data) return tenantId;
  }

  // Create demo tenant
  const { data, error } = await supabase
    .from('tenant')
    .insert({ name: 'Demo Tenant' })
    .select('id')
    .single();

  if (error) throw new Error(`Failed to create tenant: ${error.message}`);
  console.log(`Created demo tenant: ${data.id}`);
  return data.id;
}

// ─── IMPORT TO SUPABASE ───────────────────────────────────────────────────────
async function importInvoice(invoice, tenantId) {
  // Insert invoice_header
  const { data: header, error: headerErr } = await supabase
    .from('invoice_header')
    .insert({
      tenant_id: tenantId,
      invoice_number: invoice.invoice_number,
      invoice_date: invoice.invoice_date,
      customer_name: invoice.customer_name ?? null,
      customer_number: invoice.customer_number ?? null,
      total_net: invoice.total_net_amount ?? null,
      total_gross: invoice.total_gross_amount ?? null,
      currency: invoice.currency ?? 'EUR',
      status: 'pending',
      source_data: { carrier_name: invoice.carrier_name, parsing_method: 'vision_llm' },
    })
    .select('id')
    .single();

  if (headerErr) throw new Error(`Header insert failed: ${headerErr.message}`);
  console.log(`  Header inserted: ${header.id} (${invoice.invoice_number})`);

  // Insert invoice_lines
  const validLines = (invoice.lines ?? []).filter(l =>
    l.weight_kg != null || l.origin_zip != null || l.dest_zip != null
  );

  let lineCount = 0;
  for (const line of validLines) {
    const { error: lineErr } = await supabase.from('invoice_line').insert({
      tenant_id: tenantId,
      invoice_id: header.id,
      shipment_date: line.shipment_date ?? null,
      auftragsnummer: line.shipment_reference ?? null,
      tour_number: line.tour ?? null,
      origin_zip: line.origin_zip ?? null,
      origin_country: line.origin_country ?? 'DE',
      dest_zip: line.dest_zip ?? null,
      dest_country: line.dest_country ?? 'DE',
      weight_kg: line.weight_kg ?? null,
      line_total: line.line_total ?? null,
      la_code: line.billing_type ?? null,
      match_status: 'unmatched',
    });
    if (lineErr) console.warn(`  Line ${lineCount+1} warning: ${lineErr.message}`);
    else lineCount++;
  }

  console.log(`  Lines inserted: ${lineCount}/${invoice.lines?.length ?? 0}`);
  return { headerId: header.id, lineCount };
}

// ─── MAIN ─────────────────────────────────────────────────────────────────────
async function main() {
  console.log('\n' + '═'.repeat(60));
  console.log('FreightWatch Invoice Importer');
  console.log('═'.repeat(60));

  // 1. Render PDF
  const extraction = await renderPages(PDF_PATH);

  // 2. Extract with Claude
  const raw = await callClaude(extraction);

  // 3. Parse JSON
  let parsed;
  try {
    const cleaned = raw.replace(/^```(?:json)?\n?/m, '').replace(/\n?```$/m, '').trim();
    parsed = JSON.parse(cleaned);
  } catch {
    const outPath = resolve(__dirname, '../../data/invoice-raw.txt');
    writeFileSync(outPath, raw);
    console.error(`JSON parse failed. Raw saved to: ${outPath}`);
    process.exit(1);
  }

  const outPath = resolve(__dirname, '../../data/invoice-extracted.json');
  writeFileSync(outPath, JSON.stringify(parsed, null, 2));
  console.log(`\nExtracted ${parsed.invoices?.length ?? 0} invoice(s), confidence: ${parsed.confidence}`);
  if (parsed.issues?.length) console.log('Issues:', parsed.issues.join('; '));

  // 4. Insert into Supabase
  console.log('\n' + '─'.repeat(60));
  console.log('Inserting into Supabase...');

  const tenantId = await ensureTenant(TENANT_ID);
  let totalHeaders = 0, totalLines = 0;

  for (const invoice of (parsed.invoices ?? [])) {
    const { lineCount } = await importInvoice(invoice, tenantId);
    totalHeaders++;
    totalLines += lineCount;
  }

  console.log('\n' + '═'.repeat(60));
  console.log(`Done: ${totalHeaders} invoice(s), ${totalLines} line(s) imported`);
  console.log(`Extracted JSON: ${outPath}`);
  console.log('═'.repeat(60));
}

main().catch(e => { console.error('\nFatal error:', e.message); process.exit(1); });
