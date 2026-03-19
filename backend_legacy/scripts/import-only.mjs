import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createClient } from '@supabase/supabase-js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envContent = readFileSync('/Users/andreas/Repos/freightwatch/backend/.env', 'utf8');
for (const line of envContent.split('\n')) {
  const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
  if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
}

const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
const TENANT_ID = process.argv[2] || '36759ee6-e76f-45d7-9528-7fd25590052e';
const parsed = JSON.parse(readFileSync('/Users/andreas/Repos/freightwatch/data/invoice-extracted.json', 'utf8'));

console.log(`Importing ${parsed.invoices.length} invoices for tenant ${TENANT_ID}...`);

for (const invoice of parsed.invoices) {
  const { data: header, error: headerErr } = await supabase
    .from('invoice_header')
    .insert({
      tenant_id: TENANT_ID,
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
    .select('id').single();

  if (headerErr) { console.error(`Header failed: ${headerErr.message}`); continue; }

  const validLines = (invoice.lines ?? []).filter(l => l.weight_kg != null || l.origin_zip != null || l.dest_zip != null);
  let ok = 0;
  for (const line of validLines) {
    const { error } = await supabase.from('invoice_line').insert({
      tenant_id: TENANT_ID,
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
    if (!error) ok++;
    else console.warn(`  Line warn: ${error.message}`);
  }
  console.log(`✓ ${invoice.invoice_number} (${invoice.carrier_name}) → ${ok}/${invoice.lines?.length ?? 0} lines`);
}

const { data: check } = await supabase.from('invoice_header').select('id,invoice_number').eq('tenant_id', TENANT_ID);
console.log(`\nTotal in DB: ${check?.length} invoice headers`);
