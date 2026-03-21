"""
FreightWatch — tariff_extractor Prompt — Version 1.3.0

Extracts structured rate-table data from carrier tariff PDFs
(price lists, Tarifblatt, Preisliste, Konditionenblatt).
"""

VERSION = "v1.3.0"

CHANGELOG = """
v1.3.0 (2026-03-21) - MINOR: billing_conditions extraction
- ADDED: billing_conditions dict — flat key-value map of volume/weight/payment rules
  found in Volumenabrechnung, Mindestgewichte, footnote, and legend sections
- ADDED: well-known keys: ldm_to_kg, cbm_to_kg, europalette_min_kg, gitterbox_min_kg,
  ldm_trigger_pallets, ldm_pallet_size_ldm, diesel_pct, eu_mobility_pct, payment_days
- Parser maps known keys to tariff_nebenkosten typed columns; unknowns go to raw_items JSONB
- Rationale: carrier-specific billing rules are common (every carrier has LDM/pallet minimums)
  and must be extracted to calculate chargeable weight correctly

v1.2.0 (2026-03-21) - MINOR: Abstract-zone tariff support
- ADDED: tariff_type field: "plz_as_zone" | "abstract_zones"
- ADDED: zones[] range/list notation support (Python expands "80-81" → ["80","81"])

v1.1.1 (2026-03-20) - PATCH: Per-km detection heuristic for Hauptlauf
- ADDED: if Hauptlauf rate value < 5.00, classify as per-km

v1.1.0 (2026-03-20) - MINOR: Three-section tariff model
- ADDED: Direkt (zone=-1), Vor-/Nachlauf (zone>0), Hauptlauf (zone=0)

v1.0.0 (2026-03-20) - Initial version
"""

SYSTEM_PROMPT = (
    "You are a precise data extraction engine for German freight carrier tariff sheets. "
    "Your output MUST be a single valid JSON object — no markdown, no explanation, no code fences."
)

PROMPT_TEMPLATE = """\
Extract the rate table from this freight carrier tariff document.

━━━ STEP 1 — IDENTIFY TARIFF TYPE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two types exist. You MUST identify which type this document uses:

  TYPE A — plz_as_zone
    The rate table columns ARE the PLZ prefixes themselves.
    Column headers look like: 35, 60, 70, 80, 90 ...
    No separate PLZ lookup table exists.
    Rule: zone integer = PLZ prefix value (e.g. column "35" → zone 35).

  TYPE B — abstract_zones
    The rate table has columns labeled "Zone 1", "Zone 2", ... (small integers 1–N).
    A SEPARATE PLZ mapping table appears in the document header or sidebar, listing
    which PLZ prefixes belong to each zone.
    Rule: use the zone number as-is (1, 2, 3 ...). Read ALL rows of the PLZ mapping
    table for each zone column and emit them in zones[].

━━━ STEP 2 — EXTRACT SECTIONS (both types) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

German LTL tariffs have up to three sections — extract all that are present:

  Vor-/Nachlauf  (zone > 0)  — local pickup / delivery, priced by zone × weight band.
  Hauptlauf      (zone = 0)  — trunk-haul transport between hubs.
                               Lighter bands: flat rate per shipment (rate_per_shipment).
                               Heavy / open-ended band: per-km rate stored in rate_per_kg.
  Direktverkehr  (zone = -1) — direct-service rates, if present.

Per-km detection rule for Hauptlauf (IMPORTANT):
  If the open-ended heavy band shows a value < 5.00, it is a per-km rate by convention.
  Store it in rate_per_kg and set rate_per_shipment = null.

━━━ STEP 3 — zones[] RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  TYPE A:  Emit one entry per rate-table column.
           zone integer = PLZ prefix as integer.  plz_prefix = column header string.
           Example: {{"plz_prefix": "35", "zone": 35}}

  TYPE B:  Read the PLZ mapping table. For each (PLZ group, zone) entry, emit one
           zones[] item. Ranges and comma-separated lists are fine in plz_prefix.
           Example: {{"plz_prefix": "80-81", "zone": 1}},
                    {{"plz_prefix": "85-86, 89", "zone": 1}},
                    {{"plz_prefix": "82-84", "zone": 2}}
           Include ALL zones and ALL PLZ groups. Do NOT add entries for zone 0 or -1.

━━━ STEP 4 — billing_conditions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Look for a "Volumenabrechnung", "Palettenmindestgewichte", footnote, or legend block.
Extract all numeric billing parameters as a flat dict.

Use these key names when you recognise the concept:
  "ldm_to_kg"           — kg equivalent of 1 LDM          (e.g. 1250)
  "cbm_to_kg"           — kg equivalent of 1 cbm/m²        (e.g. 200)
  "europalette_min_kg"  — minimum billable kg per Euro pallet  (e.g. 150)
  "gitterbox_min_kg"    — minimum billable kg per Gitterbox     (e.g. 250)
  "ldm_trigger_pallets" — pallet positions that trigger LDM billing  (e.g. 4)
  "ldm_pallet_size_ldm" — LDM per pallet position for the trigger   (e.g. 0.4)
  "diesel_pct"          — diesel surcharge percentage        (e.g. 27.5)
  "eu_mobility_pct"     — EU mobility package surcharge %
  "payment_days"        — payment terms in days              (e.g. 30)

For any numeric rule not in this list: use a descriptive snake_case key.
Omit parameters not found. Values are always numbers, never strings.

━━━ OUTPUT SCHEMA ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{{
  "tariff_type": "plz_as_zone",
  "carrier_name": "string or null",
  "customer_name": "string or null",
  "valid_from": "YYYY-MM-DD or null",
  "currency": "EUR or other 3-letter code",
  "lane_type": "domestic_de",
  "zones": [
    {{"plz_prefix": "35", "zone": 35}},
    {{"plz_prefix": "60", "zone": 60}}
  ],
  "rates": [
    {{
      "zone": 35,
      "weight_from_kg": 0,
      "weight_to_kg": 300,
      "rate_per_shipment": 56.13,
      "rate_per_kg": null
    }},
    {{
      "zone": 0,
      "weight_from_kg": 0,
      "weight_to_kg": 1500,
      "rate_per_shipment": 530.00,
      "rate_per_kg": null
    }},
    {{
      "zone": 0,
      "weight_from_kg": 1500,
      "weight_to_kg": 99999,
      "rate_per_shipment": null,
      "rate_per_kg": 0.1100
    }}
  ],
  "billing_conditions": {{
    "ldm_to_kg": 1250,
    "cbm_to_kg": 200,
    "europalette_min_kg": 150
  }},
  "confidence": 0.0,
  "issues": []
}}

━━━ ADDITIONAL RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- rates[]: one entry per (zone, weight band) cell for ALL sections.
  weight_from_kg exclusive lower bound; weight_to_kg inclusive upper.
  First band "< 300 kg": weight_from_kg=0, weight_to_kg=300.
  Open-ended ""> 1500 kg": weight_from_kg=1500, weight_to_kg=99999.
  A "Mindestbetrag" row is NOT a weight band — skip it.
- valid_from: look for "ab 01.04.2022", "gültig ab", "Version MM.YYYY".
  Convert German dates (dd.mm.yyyy) to ISO. "Version 01.2022" → 2022-01-01.
- carrier_name: the company issuing the tariff (footer / sender).
- customer_name: the recipient company named on the document (if any).
- currency: usually EUR; extract from document if present.
- confidence: your confidence in the extraction quality (0.0–1.0).
- issues: list any data quality problems or unclear values.

Document text:
{text}"""
