/**
 * Vision-based tariff PDF extractor for German freight carrier tariff sheets.
 *
 * Renders each PDF page to an image with mupdf, sends all pages to Claude Vision
 * in a single API call, and extracts structured tariff data matching the schema
 * of tariff-extraction.json (zones, price matrix, PLZ map, Nebenkosten, Maut).
 *
 * Usage (from backend/):
 *   node scripts/extract-tariff-pdf.mjs <pdf-path> [output-path]
 *
 * Requires: ANTHROPIC_API_KEY in backend/.env (or env)
 */
import { writeFileSync, readFileSync } from 'fs';
import { resolve, basename, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __dirname = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

// Load .env manually (no dotenv dependency needed – parse key=value lines)
try {
  const envPath = resolve(__dirname, '../.env');
  const envContent = readFileSync(envPath, 'utf8');
  for (const line of envContent.split('\n')) {
    const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
} catch { /* .env optional */ }

const pdfPath = process.argv[2];
const outputPath = process.argv[3] ?? resolve(__dirname, '../../data/gebr-weiss-tariff-extraction.json');

if (!pdfPath) {
  console.error('Usage: node scripts/extract-tariff-pdf.mjs <pdf-path> [output-path]');
  process.exit(1);
}

if (!process.env.ANTHROPIC_API_KEY) {
  console.error('ANTHROPIC_API_KEY not set');
  process.exit(1);
}

const RENDER_SCALE = 2;

// ─── RENDER PDF PAGES ────────────────────────────────────────────────────────

async function renderPages(pdfPath) {
  const mupdf = await import('mupdf');
  const buffer = readFileSync(pdfPath);
  const doc = mupdf.Document.openDocument(new Uint8Array(buffer), 'application/pdf');
  const pageCount = doc.countPages();

  console.log(`\nFile: ${basename(pdfPath)}`);
  console.log(`Pages: ${pageCount}`);
  console.log('─'.repeat(60));

  // Try text extraction first
  const textParts = [];
  let totalChars = 0;
  for (let i = 0; i < pageCount; i++) {
    const text = doc.loadPage(i).toStructuredText('preserve-whitespace').asText();
    textParts.push(text);
    totalChars += text.trim().length;
  }
  const avgChars = totalChars / pageCount;
  const emptyCount = textParts.filter((t) => t.trim().length < 20).length;
  const emptyFraction = emptyCount / pageCount;

  console.log(`Avg chars/page: ${Math.round(avgChars)}, empty pages: ${emptyCount}/${pageCount}`);

  if (avgChars >= 50 && emptyFraction < 0.5) {
    console.log('Mode: TEXT (selectable PDF)');
    return { mode: 'text', text: textParts.join('\n\n---PAGE---\n\n'), pageCount };
  }

  console.log('Mode: VISION – rendering pages as images...');
  const pages = [];
  for (let i = 0; i < pageCount; i++) {
    const page = doc.loadPage(i);
    const matrix = [RENDER_SCALE, 0, 0, RENDER_SCALE, 0, 0];
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
  return { mode: 'vision', pages, pageCount };
}

// ─── PROMPT ──────────────────────────────────────────────────────────────────

function buildPrompt(pageCount, mode, textContent) {
  const intro = mode === 'text'
    ? `You are analyzing the text content of a German freight carrier tariff sheet (Tarifblatt / Preisliste).`
    : `You are analyzing ${pageCount} page(s) of a German freight carrier tariff sheet (Tarifblatt / Preisliste).`;

  const textSection = mode === 'text'
    ? `\n\nExtracted text content:\n\`\`\`\n${textContent}\n\`\`\``
    : '';

  return `${intro}

IMPORTANT: Process EVERY page and EVERY table in the document. Do not skip any section.

Extract ALL tariff data and return ONLY a JSON object – no markdown, no explanation, no code fences.${textSection}

Required JSON structure (use null for any top-level field not present in the document):
{
  "meta": {
    "title": "string or null",
    "service_type": "string describing service (e.g. Stückgutversand Deutschland) or null",
    "tariff_country": "ISO 3166-1 alpha-2 country code of the DESTINATION country (e.g. 'DE', 'AT', 'CH'). Infer from service description: 'Stückgutversand Österreich' → 'AT', 'Stückgutversand Deutschland' → 'DE', 'Schweiz' → 'CH'. For multi-country documents use the primary country.",
    "origin": "departure location (e.g. ab Velbert) or null",
    "valid_from": "DD.MM.YYYY or null",
    "valid_until": "DD.MM.YYYY or null",
    "carrier_name": "string or null",
    "carrier_address": "string or null",
    "customer_name": "string or null",
    "customer_address": "string or null"
  },
  "tariff": {
    "zones": [
      {
        "zone_number": 1,
        "label": "Zone I or similar",
        "plz_description": "Compact summary derived from this zone's plz_zone_map entries, e.g. '42, 44, 50-51, 58-59'"
      }
    ],
    "matrix": [
      {
        "band_label": "bis 200 kg or similar as printed",
        "weight_from": 1,
        "weight_to": 200,
        "prices": {
          "zone_1": 52.00,
          "zone_2": 61.50
        }
      }
    ],
    "plz_zone_map": [
      {
        "country_code": "DE",
        "plz_prefix": "42",
        "match_type": "prefix",
        "zone": 1
      }
    ],
    "plz_lookup_rules": {
      "priority": "exact_before_prefix",
      "description": "When resolving a PLZ to a zone, first check for an exact full-length match, then fall back to the longest matching prefix."
    }
  },
  "nebenkosten": {
    "typed": {
      "diesel_floater_pct": 18.5,
      "eu_mobility_surcharge_pct": null,
      "maut_included": false,
      "delivery_condition": "frei Haus or ab Werk or DDP etc., or null",
      "oversize_note": "string or null",
      "min_weight_half_pallet_kg": null,
      "min_weight_pallet_kg": null,
      "min_weight_per_cbm_kg": null,
      "min_weight_per_ldm_kg": null,
      "min_weight_small_format_kg": null,
      "min_weight_medium_format_kg": null,
      "min_weight_large_format_kg": null,
      "pallet_exchange_euro_flat": null,
      "pallet_exchange_euro_mesh": null,
      "return_pickup": "string or null",
      "transport_insurance": "string or null",
      "hazmat_surcharge": "text describing Gefahrgutzuschlag/dangerous goods surcharge, e.g. '15% der Fracht, min. 15 EUR' or null",
      "liability_surcharge": "text describing Haftungszuschlag for waiver customers only, or null",
      "manual_order_fee_euro": null,
      "avis_neutralization_fee_euro": null,
      "island_trade_fair_surcharge": "string or null",
      "payment_days": null,
      "payment_terms": "full text of payment terms e.g. 'sofort ohne Abzug' or '20 Tage netto' or null",
      "billing_cycle": "string or null",
      "legal_basis": "string or null"
    },
    "raw": [
      { "label": "original label text", "value": "original value text or null" }
    ]
  },
  "maut": null,
  "lsva": null,
  "city_surcharges": null,
  "confidence": 0.0,
  "issues": ["list any data quality problems"]
}

── SECTION ROUTING (which data goes where) ──────────────────────────────────
- tariff.matrix: ONLY freight price tables (Frachttarif). If no freight price table exists in the document, set tariff.matrix to [].
- maut: Maut/toll surcharge tables. If present, use this structure (do NOT put Maut data in tariff.matrix):
  {
    "tables": [
      {
        "label": "title as printed, e.g. 'Maut-Tarif bis 3000 kg'",
        "weight_limit_kg": 3000,
        "weight_from_kg": 1,
        "minimum_charge_eur": 2.60,
        "distance_ranges": ["001-100 km", "101-200 km"],
        "matrix": [
          {
            "weight_from": 1, "weight_to": 50,
            "prices": { "001-100 km": 2.60, "101-200 km": 3.20 }
          }
        ],
        "plz_zone_map": [
          { "country_code": "DE", "plz_prefix": "78", "match_type": "prefix", "zone": 1 }
        ]
      }
    ]
  }
  Note: Maut zones are distance-based (km ranges), not geographic PLZ zones. The plz_zone_map under maut maps PLZ prefixes to distance zones.
  Note: weight_from_kg for the first table is always 1. For each subsequent table (e.g. "ab 3001 kg"), weight_from_kg = previous table's weight_limit_kg + 1. weight_limit_kg for the last table should be the maximum weight stated or null if unbounded.
- lsva: Swiss LSVA (Leistungsabhängige Schwerverkehrsabgabe) table. If present:
  {
    "currency": "CHF",
    "valid_from": "DD.MM.YYYY or null",
    "billing_unit_above_threshold": "per 100 kg or per shipment etc.",
    "weight_threshold_kg": 500,
    "zones": [ { "zone_number": 1, "plz_description": "..." } ],
    "matrix": [
      {
        "band_label": "bis 30 kg", "weight_from": 1, "weight_to": 30,
        "prices": { "zone_1": 12.50 }
      }
    ],
    "plz_zone_map": [
      { "country_code": "CH", "plz_prefix": "10", "match_type": "prefix", "zone": 1 }
    ]
  }
- city_surcharges: Großstadtzuschläge table mapping cities to PLZ ranges. If present:
  {
    "surcharge_pct": 10,
    "surcharge_note": "string or null",
    "cities": [
      {
        "city": "Berlin",
        "plz_ranges": [
          { "country_code": "DE", "plz_from": "10115", "plz_to": "14199" }
        ]
      }
    ]
  }

── EXTRACTION RULES ─────────────────────────────────────────────────────────
- Numbers: remove German thousand separators (period), use period as decimal separator. E.g. "1.250" → 1250, "62,50" → 62.50
- Weight bands: extract weight_from (previous band + 1) and weight_to from band label. E.g. "bis 200 kg" → weight_from:1, weight_to:200; next "bis 300 kg" → weight_from:201, weight_to:300
- Zone keys in prices objects must be "zone_1", "zone_2" etc. matching zone_number; for distance-based Maut use the range string as key (e.g. "001-100 km")

── PLZ MAP RULES (critical for correctness) ─────────────────────────────────
STEP 1 – Determine country and apply correct PLZ prefix length:
  - DE (Germany):  5-digit PLZ → 2-digit prefixes (zero-pad: "1" → "01"). country_code: "DE"
  - AT (Austria):  4-digit PLZ → 1-digit prefixes ("1"–"9"). country_code: "AT"
  - CH (Switzerland): 4-digit PLZ → 2-digit prefixes ("10"–"99"). country_code: "CH"
  - Other: use the natural prefix length for that country's postal system.

STEP 2 – Expand every printed range into individual prefix entries. Do NOT skip any prefix. E.g. "37-42" → separate entries for 37, 38, 39, 40, 41, 42. E.g. AT "4+5" → entries for "4" and "5".

STEP 3 – Full-length postal codes use match_type "exact"; all shorter values use match_type "prefix".

STEP 4 – Derive plz_description for each zone solely from that zone's plz_zone_map entries.

STEP 5 – Multi-country documents: include all countries in the same plz_zone_map, each with correct country_code and prefix length. If no PLZ mapping exists, set plz_zone_map to [].

── NEBENKOSTEN TYPED FIELDS ─────────────────────────────────────────────────
CRITICAL – two fields that are frequently confused:
- hazmat_surcharge: ONLY for Gefahrgutzuschlag / ADR / dangerous goods surcharge. Typical text: "15% der Fracht, Minimum 15 EUR, Maximum 100 EUR". Keywords: Gefahrgut, ADR, gefährliche Güter.
- liability_surcharge: ONLY for Haftungszuschlag / Verzichtskunde surcharge. Applies when customer waives carrier liability. Keywords: Haftung, Verzichtskunde, Haftungsverzicht.
- If a row mentions "Gefahrgut" → hazmat_surcharge. If a row mentions "Haftung" / "Verzicht" → liability_surcharge. Never put Gefahrgut text into liability_surcharge.
- eu_mobility_surcharge_pct: extract numeric % if EU Mobilitätszuschlag/Mobilitätspauschale mentioned
- maut_included: true if document states "inkl. Maut" or similar; false otherwise
- delivery_condition: "frei Haus", "ab Werk", "DDP", "DAP", etc.
- payment_terms: full condition text (e.g. "sofort ohne Abzug"); payment_days: numeric days only

- Confidence: estimate 0.0-1.0 based on legibility and completeness of ALL sections`;
}

// ─── CALL CLAUDE ─────────────────────────────────────────────────────────────

async function callClaude(extraction) {
  const { default: Anthropic } = await import('@anthropic-ai/sdk');
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  let content;

  if (extraction.mode === 'text') {
    content = [
      { type: 'text', text: buildPrompt(extraction.pageCount, 'text', extraction.text) },
    ];
  } else {
    const imageBlocks = extraction.pages.map((page) => ({
      type: 'image',
      source: { type: 'base64', media_type: 'image/png', data: page.image_base64 },
    }));
    const totalKb = extraction.pages.reduce((s, p) => s + p.size_kb, 0);
    console.log(`\nSending ${extraction.pages.length} pages (${totalKb} KB total) to Claude Vision...`);
    content = [...imageBlocks, { type: 'text', text: buildPrompt(extraction.pageCount, 'vision', null) }];
  }

  const t0 = Date.now();
  let raw = '';
  const stream = await client.messages.stream({
    model: 'claude-sonnet-4-6',
    max_tokens: 32000,
    messages: [{ role: 'user', content }],
  });
  for await (const event of stream) {
    if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
      raw += event.delta.text;
    }
  }
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

  console.log(`Claude responded in ${elapsed}s (${raw.length} chars)`);
  return raw;
}

// ─── MAIN ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log(`\nProcessing: ${basename(pdfPath)}`);
  console.log('─'.repeat(60));

  const extraction = await renderPages(pdfPath);

  const raw = await callClaude(extraction);

  let parsed;
  try {
    const cleaned = raw.replace(/^```(?:json)?\n?/m, '').replace(/\n?```$/m, '').trim();
    parsed = JSON.parse(cleaned);
  } catch {
    console.error('\nFailed to parse JSON. Raw output saved to -raw.txt');
    writeFileSync(outputPath.replace('.json', '-raw.txt'), raw);
    process.exit(1);
  }

  writeFileSync(outputPath, JSON.stringify(parsed, null, 2));

  // Summary
  const d = parsed;
  console.log('\n' + '─'.repeat(60));
  console.log(`Carrier:    ${d.meta?.carrier_name ?? '?'}`);
  console.log(`Customer:   ${d.meta?.customer_name ?? '?'}`);
  console.log(`Valid from: ${d.meta?.valid_from ?? '?'}`);
  console.log(`Zones:      ${d.tariff?.zones?.length ?? 0}`);
  console.log(`Weight bands: ${d.tariff?.matrix?.length ?? 0}`);
  console.log(`PLZ entries:  ${d.tariff?.plz_zone_map?.length ?? 0}`);
  console.log(`Confidence: ${d.confidence ?? '?'}`);
  if (d.issues?.length) console.log(`Issues: ${d.issues.join('; ')}`);
  console.log(`\nSaved to: ${outputPath}`);
}

main().catch((err) => {
  console.error('Error:', err);
  process.exit(1);
});
