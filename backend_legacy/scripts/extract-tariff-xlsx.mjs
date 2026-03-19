/**
 * General-purpose tariff Excel extractor for German freight carrier tariffs.
 *
 * Handles the common structure:
 *   Sheet "Tarif"      – price matrix (weight bands × zones) + PLZ→zone mapping
 *   Sheet "Nebenkosten"– surcharges and special conditions
 *   Sheet "Maut"       – toll table (weight bands × distance ranges)
 *
 * Robustness rules applied:
 *   - German thousand separator (period): detected and converted to integer
 *   - PLZ prefixes: ≤4-digit values are zone prefixes (zero-padded to 2 digits),
 *     5-digit values are exact PLZ matches
 *   - Nebenkosten: captured as generic key/value pairs (not hardcoded label search)
 *     so new rows in future documents are not silently dropped
 *   - Phantom rows (all-null or zero weights): filtered out
 *
 * Usage (from backend/):
 *   node scripts/extract-tariff-xlsx.mjs <xlsx-path> [output-path]
 */
import { writeFileSync } from 'fs';
import { createRequire } from 'module';
import { resolve, basename } from 'path';

const require = createRequire(import.meta.url);
const XLSX = require('xlsx');

const xlsxPath = process.argv[2];
const outputPath = process.argv[3] ?? resolve(process.cwd(), '../../data/tariff-extraction.json');

if (!xlsxPath) {
  console.error('Usage: node scripts/extract-tariff-xlsx.mjs <xlsx-path> [output-path]');
  process.exit(1);
}

const wb = XLSX.readFile(xlsxPath);
console.log(`\nFile: ${basename(xlsxPath)}`);
console.log(`Sheets: ${wb.SheetNames.join(', ')}`);
console.log('─'.repeat(60));

// ─── HELPERS ────────────────────────────────────────────────────

/**
 * Parse a cell value that may contain a German-formatted integer.
 *
 * xlsx.js reads "1.001" (German thousands separator) as the float 1.001.
 * We detect this by checking:
 *   - value is a number with a fractional part
 *   - fractional part has exactly 3 significant decimal digits
 *   - multiplying by 1000 yields a near-integer
 *
 * This does NOT affect legitimate decimals like prices (e.g. 56.13),
 * because those do not have exactly 3 decimal digits after removing trailing zeros.
 */
function parseGermanInteger(val) {
  if (val === '' || val === null || val === undefined) return null;
  const n = Number(String(val).replace(',', '.'));
  if (isNaN(n)) return null;

  // Detect German thousands separator: e.g. 1.001 → 1001, 1.250 → 1250
  // A value like 1.001 has exactly 3 decimal digits with no trailing-zero ambiguity.
  // We check: frac * 1000 is a near-integer AND the result would be a plausible weight (> n).
  const frac = n - Math.floor(n);
  if (frac > 0) {
    const candidate = Math.round(n * 1000);
    const reconstructed = candidate / 1000;
    // Accept if the float representation matches within floating-point noise
    if (Math.abs(reconstructed - n) < 0.0001 && candidate > n) {
      return candidate;
    }
  }
  return Math.round(n);
}

/**
 * Normalise a PLZ / zone-prefix value from the Excel mapping table.
 *
 * Column A contains either:
 *   - A short prefix (1-4 digits): e.g. 1 → "01", 42 → "42", 82 → "82"
 *   - An exact 5-digit PLZ: e.g. 61118, 82219
 *
 * The PLZ Lang column (B) confirms the interpretation:
 *   prefix  → plz_lang < 100000 (e.g. 1000, 42000)
 *   exact   → plz_lang === plz_value (e.g. 61118 === 61118)
 */
function parsePlzEntry(rawPlz, rawPlzLang) {
  const plzNum = Number(rawPlz);
  const langNum = Number(rawPlzLang);

  if (isNaN(plzNum) || plzNum <= 0) return null;

  // 5-digit exact PLZ: column A and B are the same 5-digit number
  if (plzNum >= 10000 && plzNum === langNum) {
    return { plz_prefix: String(plzNum), match_type: 'exact' };
  }

  // Short prefix (1-4 digits): zero-pad to 2 digits for consistent matching
  // e.g. 1 → "01", 8 → "08", 42 → "42", 99 → "99"
  const prefixStr = String(plzNum).padStart(2, '0');
  return { plz_prefix: prefixStr, match_type: 'prefix' };
}

/**
 * Extract a price from a cell or label value.
 * Handles:
 *   - Pure numbers: 56.13, 9.5
 *   - German decimal: "9,50"
 *   - Numbers embedded in text: "pro Auftrag 4,50 Euro", "16,50 €je Sendung"
 *   - Currency symbol before or after
 */
function parsePrice(val) {
  if (val === '' || val === null || val === undefined) return null;
  const s = String(val);
  // Find the first decimal/integer number in the string (handles embedded text)
  const m = s.match(/(\d+)[,.](\d+)/);
  if (m) return parseFloat(`${m[1]}.${m[2]}`);
  const m2 = s.match(/(\d+)/);
  if (m2) return parseInt(m2[1]);
  return null;
}

/** Extract the numeric part from a string like "200 kg", "1.500 kg". */
function parseWeightStr(str) {
  if (!str) return null;
  const cleaned = String(str).replace(/\./g, '').replace(',', '.');
  const match = cleaned.match(/(\d+)/);
  return match ? parseInt(match[1]) : null;
}

/** Extract the upper weight bound from a label like "bis 200 kg", "bis 5.000 kg". */
function extractWeightTo(label) {
  const match = String(label).match(/(\d[\d.]*)\s*kg/);
  if (!match) return null;
  return parseInt(match[1].replace(/\./g, ''));
}

// ─── TARIF SHEET ────────────────────────────────────────────────
const tariffSheet = wb.Sheets['Tarif'];
const tariffRows = XLSX.utils.sheet_to_json(tariffSheet, { header: 1, defval: '' });

// --- Metadata: scan first rows for title/date/carrier/customer ----
// We look for the first non-empty rows rather than assuming fixed positions.
const metaLines = tariffRows.slice(0, 10).map(r => String(r[0] || '').trim()).filter(Boolean);
const meta = {
  title: metaLines[0] ?? null,
  service_type: metaLines[1] ?? null,
  origin: metaLines[2] ?? null,
  valid_from: (metaLines[3] ?? '').replace(/Stand\s*:\s*/i, '').trim() || null,
};

// Carrier (col 0) and customer (col 5) appear in consecutive rows:
//   row N:   company name  |  company name
//   row N+1: street        |  street
//   row N+2: PLZ + city    |  PLZ + city
// We track the last street seen (col 0 / col 5) before the PLZ row.
const companyRe = /\bGmbH\b|\bKG\b|\bAG\b|\be\.K\.\b|\bSpedition\b/i;
let lastStreet0 = null;
let lastStreet5 = null;
for (const row of tariffRows.slice(0, 12)) {
  const col0 = String(row[0] || '').trim();
  const col5 = String(row[5] || '').trim();

  if (companyRe.test(col0)) {
    meta.carrier_name = col0;
    if (companyRe.test(col5)) meta.customer_name = col5;
  }

  // Track street-like rows (non-empty, no company pattern, no 5-digit PLZ start)
  if (col0 && !companyRe.test(col0) && !/^\d{5}\b/.test(col0)) lastStreet0 = col0;
  if (col5 && !companyRe.test(col5) && !/^\d{5}\b/.test(col5)) lastStreet5 = col5;

  // PLZ row: combine street + PLZ/city into full address
  if (/^\d{5}\b/.test(col0)) {
    meta.carrier_address = lastStreet0 ? `${lastStreet0}, ${col0}` : col0;
  }
  if (/^\d{5}\b/.test(col5)) {
    meta.customer_address = lastStreet5 ? `${lastStreet5}, ${col5}` : col5;
  }
}

console.log('Carrier:', meta.carrier_name ?? '(not found)');
console.log('Customer:', meta.customer_name ?? '(not found)');
console.log('Valid from:', meta.valid_from ?? '(not found)');

// --- Locate the price matrix header row (contains "Zone") --------
let matrixHeaderRow = -1;
for (let r = 0; r < tariffRows.length; r++) {
  const rowStr = tariffRows[r].join(' ');
  if (/Zone/i.test(rowStr)) { matrixHeaderRow = r; break; }
}

// Zone labels and numbers
const zoneLabels = matrixHeaderRow >= 0
  ? tariffRows[matrixHeaderRow].slice(2).map(v => String(v).trim()).filter(v => /Zone/i.test(v))
  : [];

// Zone number row is typically 4 rows after the label row
let zoneNumberRow = -1;
for (let r = matrixHeaderRow + 1; r < Math.min(matrixHeaderRow + 6, tariffRows.length); r++) {
  const cells = tariffRows[r].slice(2).filter(v => v !== '');
  if (cells.length > 0 && cells.every(v => !isNaN(Number(v)))) {
    zoneNumberRow = r;
    break;
  }
}
const zoneNumbers = zoneNumberRow >= 0
  ? tariffRows[zoneNumberRow].slice(2, 2 + zoneLabels.length).map(v => Number(v))
  : zoneLabels.map((_, i) => i + 1);

console.log(`\nZones: ${zoneLabels.join(', ')}`);

// --- Price matrix rows: detect by "bis X kg" pattern in col 0 ---
const tariffMatrix = [];
for (let r = matrixHeaderRow + 1; r < tariffRows.length; r++) {
  const row = tariffRows[r];
  const label = String(row[0] || '').trim();
  if (!/bis\s+\d/i.test(label)) continue;

  const weightFrom = parseGermanInteger(row[1]);
  const weightTo = extractWeightTo(label);

  const prices = {};
  for (let z = 0; z < zoneLabels.length; z++) {
    const raw = row[2 + z];
    const isUnavailable = String(raw).trim().toLowerCase() === 'xx';
    const parsed = parsePrice(raw);
    prices[`zone_${z + 1}`] = isUnavailable ? null : (parsed !== null ? parsed : (Number(raw) || null));
  }

  tariffMatrix.push({ band_label: label, weight_from: weightFrom, weight_to: weightTo, prices });
}
console.log(`Tariff matrix: ${tariffMatrix.length} weight bands × ${zoneLabels.length} zones`);

// --- PLZ → Zone mapping: detect by "PLZ" header row ---------------
let plzHeaderRow = -1;
for (let r = matrixHeaderRow + 1; r < tariffRows.length; r++) {
  if (String(tariffRows[r][0]).trim().toUpperCase() === 'PLZ') {
    plzHeaderRow = r;
    break;
  }
}

const plzZoneMap = [];
if (plzHeaderRow >= 0) {
  for (let r = plzHeaderRow + 1; r < tariffRows.length; r++) {
    const row = tariffRows[r];
    if (row[0] === '' || row[0] === null) continue;
    const entry = parsePlzEntry(row[0], row[1]);
    const zone = Number(row[2]);
    if (entry && zone > 0) plzZoneMap.push({ ...entry, zone });
  }
}
console.log(`PLZ→Zone mapping: ${plzZoneMap.length} entries`);

// --- Zone text descriptions (rows between matrix header and zone-number row) ---
const zoneDescriptions = {};
if (matrixHeaderRow >= 0 && zoneNumberRow > matrixHeaderRow) {
  for (let r = matrixHeaderRow + 1; r < zoneNumberRow; r++) {
    const row = tariffRows[r];
    for (let z = 0; z < zoneLabels.length; z++) {
      const cell = String(row[2 + z] || '').trim();
      if (cell) {
        const key = `zone_${z + 1}`;
        // Accumulate; cleaned up after the loop
        zoneDescriptions[key] = zoneDescriptions[key]
          ? `${zoneDescriptions[key]} ${cell}`
          : cell;
      }
    }
  }
}

// Clean up plz_description: collapse whitespace, remove redundant commas/dashes
for (const key of Object.keys(zoneDescriptions)) {
  zoneDescriptions[key] = zoneDescriptions[key]
    .replace(/\s*,\s*/g, ', ')   // normalise comma spacing
    .replace(/,\s*,/g, ',')       // remove double commas
    .replace(/\s{2,}/g, ' ')      // collapse whitespace
    .trim();
}

// ─── NEBENKOSTEN SHEET ──────────────────────────────────────────
// Strategy: capture ALL non-empty rows as generic key/value pairs.
// Also extract well-known fields into typed properties for easy consumption.
const nkSheet = wb.Sheets['Nebenkosten'];
const nkRows = XLSX.utils.sheet_to_json(nkSheet, { header: 1, defval: '' });

const nebenkostenRaw = [];      // All rows – for completeness
const nebenkostenTyped = {};    // Well-known typed fields

for (const row of nkRows) {
  const label = String(row[0] || '').trim();
  const val1  = String(row[1] || '').trim();
  const val2  = String(row[2] || '').trim();
  const val3  = String(row[3] || '').trim();

  if (!label) continue;

  // Capture everything as raw K/V for future use
  const value = [val1, val2, val3].filter(Boolean).join(' | ') || null;
  nebenkostenRaw.push({ label, value });

  // Typed extraction of common fields
  const labelLower = label.toLowerCase();

  if (labelLower.includes('dieselfloater')) {
    const m = label.match(/([\d,]+)\s*%/);
    if (m) nebenkostenTyped.diesel_floater_pct = parseFloat(m[1].replace(',', '.'));
  }
  if (labelLower.includes('mindestgewicht') && labelLower.includes('halbpalette')) {
    nebenkostenTyped.min_weight_half_pallet_kg = parseWeightStr(val2 || val1);
  }
  if (labelLower.includes('mindestgewicht') && labelLower.includes('palette') && !labelLower.includes('halb')) {
    nebenkostenTyped.min_weight_pallet_kg = parseWeightStr(val2 || val1);
  }
  if (labelLower.includes('mindestgewicht') && labelLower.includes('kubikmeter')) {
    nebenkostenTyped.min_weight_per_cbm_kg = parseWeightStr(val2 || val1);
  }
  if (labelLower.includes('mindestgewicht') && labelLower.includes('lademeter')) {
    nebenkostenTyped.min_weight_per_ldm_kg = parseWeightStr(val2 || val1);
  }
  if (labelLower.includes('mindestgewicht') && labelLower.includes('kleinformat')) {
    nebenkostenTyped.min_weight_small_format_kg = parseWeightStr(val2 || val1);
  }
  if (labelLower.includes('mindestgewicht') && labelLower.includes('mittelformat')) {
    nebenkostenTyped.min_weight_medium_format_kg = parseWeightStr(val2 || val1);
  }
  // "Großformat" detection: tolerates OCR-like variants: "Großformat", "Gr0ßformat", "Grossformat"
  if (labelLower.includes('mindestgewicht') && labelLower.includes('format') &&
      !labelLower.includes('klein') && !labelLower.includes('mittel')) {
    nebenkostenTyped.min_weight_large_format_kg = parseWeightStr(val2 || val1);
  }
  if (labelLower.includes('palettentausch') && (labelLower.includes('euroflach') || (val2 || val1).toLowerCase().includes('euroflach'))) {
    nebenkostenTyped.pallet_exchange_euro_flat = parsePrice(val2 || val1);
  }
  if (labelLower.includes('palettentausch') && (labelLower.includes('gitterbox') || (val2 || val1).toLowerCase().includes('gitterbox'))) {
    nebenkostenTyped.pallet_exchange_euro_mesh = parsePrice(val2 || val1);
  }
  if (labelLower.includes('manuelle auftragserfassung') || labelLower.includes('manuelle auftrags')) {
    nebenkostenTyped.manual_order_fee_euro = parsePrice(val2 || val1);
  }
  if (labelLower.includes('neutralisierung') || labelLower.includes('avis') || labelLower.includes('zeitfenster')) {
    const p = parsePrice(val2 || val1);
    if (p) {
      // Preserve the "jeweils" semantics: fee applies per service, not per bundle
      const isJeweils = label.toLowerCase().includes('jeweils');
      nebenkostenTyped.avis_neutralization_fee_euro = p;
      nebenkostenTyped.avis_neutralization_per_service = isJeweils;
    }
  }
  if (labelLower.includes('rückholung') || labelLower.includes('abholauftrag')) {
    nebenkostenTyped.return_pickup = val2 || val1 || null;
  }
  if (labelLower.includes('speditionsversicherung') || labelLower.includes('transportversicherung')) {
    nebenkostenTyped.transport_insurance = val2 || val1 || null;
  }
  if (labelLower.includes('haftungszuschlag')) {
    nebenkostenTyped.liability_surcharge = val2 || val1 || null;
  }
  if (labelLower.includes('insel') || labelLower.includes('messe')) {
    nebenkostenTyped.island_trade_fair_surcharge = val2 || val1 || null;
  }
  if (labelLower.includes('zahlungsziel')) {
    const days = label.match(/(\d+)\s*tage/i);
    if (days) nebenkostenTyped.payment_days = parseInt(days[1]);
    if (val1.toLowerCase().includes('abrechnung')) {
      nebenkostenTyped.billing_cycle = val1.replace(/Abrechnung\s*:\s*/i, '').trim();
    }
    const until = [val1, val2, val3].find(v => v.toLowerCase().includes('gültig'));
    if (until) nebenkostenTyped.valid_until = until.replace(/Gültig bis\s*/i, '').trim();
  }
  if (labelLower.includes('3.000 kg') || labelLower.includes('3000 kg') ||
      labelLower.includes('7 mtr') || labelLower.includes('tagespreis')) {
    nebenkostenTyped.oversize_note = label + (val1 ? ` ${val1}` : '');
  }
  if (labelLower.includes('adsp') || labelLower.includes('spediteurbedingungen')) {
    nebenkostenTyped.legal_basis = label;
  }
}

console.log(`\nDiesel floater: ${nebenkostenTyped.diesel_floater_pct ?? '(not found)'}%`);
console.log(`Valid until: ${nebenkostenTyped.valid_until ?? '(not found)'}`);
console.log(`Nebenkosten rows captured: ${nebenkostenRaw.length}`);

// ─── MAUT SHEET ─────────────────────────────────────────────────
const mautSheet = wb.Sheets['Maut'];
const mautRows = XLSX.utils.sheet_to_json(mautSheet, { header: 1, defval: '' });

// Find the header row: contains distance ranges (e.g. "001-100", "101-200")
let mautHeaderRow = -1;
for (let r = 0; r < mautRows.length; r++) {
  const rowStr = mautRows[r].join(' ');
  if (/\d{3}-\d{3}/.test(rowStr) || /ab\s+\d+/i.test(rowStr)) {
    mautHeaderRow = r;
    break;
  }
}

const mautDistanceHeaders = mautHeaderRow >= 0
  ? mautRows[mautHeaderRow].slice(2).map(v => String(v).trim()).filter(Boolean)
  : [];

const mautMatrix = [];
if (mautHeaderRow >= 0) {
  for (let r = mautHeaderRow + 1; r < mautRows.length; r++) {
    const row = mautRows[r];

    const weightFrom = parseGermanInteger(row[0]);
    const weightTo   = parseGermanInteger(row[1]);

    // Skip phantom rows (both bounds null/zero or all prices null)
    if (!weightFrom && !weightTo) continue;
    if (weightFrom === 0 && weightTo === 0) continue;

    const prices = {};
    let hasAnyPrice = false;
    mautDistanceHeaders.forEach((dist, i) => {
      const p = parsePrice(row[2 + i]);
      prices[dist] = p;
      if (p !== null) hasAnyPrice = true;
    });

    if (!hasAnyPrice) continue; // skip entirely-empty rows

    // Correct "round thousand" weight_to values misread as small integers.
    // Root cause: Excel "1.000" (German thousands) → JS float 1.0 → integer 1.
    // Detection: weight_to < weight_from is impossible in a monotone table.
    let correctedTo = weightTo;
    if (weightTo !== null && weightFrom !== null && weightTo < weightFrom) {
      correctedTo = weightTo * 1000;
    }

    mautMatrix.push({ weight_from: weightFrom, weight_to: correctedTo, prices });
  }
}
console.log(`Maut matrix: ${mautMatrix.length} weight bands × ${mautDistanceHeaders.length} distance ranges`);

// ─── ASSEMBLE OUTPUT ────────────────────────────────────────────
const output = {
  meta,
  tariff: {
    zones: zoneLabels.map((label, i) => ({
      zone_number: zoneNumbers[i] ?? i + 1,
      label,
      plz_description: zoneDescriptions[`zone_${i + 1}`] ?? null,
    })),
    matrix: tariffMatrix,
    plz_zone_map: plzZoneMap,
    plz_lookup_rules: {
      priority: 'exact_before_prefix',
      description: 'When resolving a PLZ to a zone, first check for an exact 5-digit match (match_type="exact"), then fall back to the longest matching prefix (match_type="prefix"). Example: PLZ 82219 → zone 7 (exact), PLZ 82345 → zone 6 (prefix "82").',
    },
  },
  nebenkosten: {
    typed: nebenkostenTyped,
    raw: nebenkostenRaw,
  },
  maut: {
    distance_ranges: mautDistanceHeaders,
    matrix: mautMatrix,
  },
  extracted_at: new Date().toISOString(),
  source_file: basename(xlsxPath),
};

writeFileSync(outputPath, JSON.stringify(output, null, 2));
console.log(`\nSaved to: ${outputPath}`);
