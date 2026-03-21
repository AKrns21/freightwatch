"""
FreightWatch — tariff_extractor Prompt — Version 1.2.0

Extracts structured rate-table data from carrier tariff PDFs
(price lists, Tarifblatt, Preisliste, Konditionenblatt).
"""

VERSION = "v1.2.0"

CHANGELOG = """
v1.2.0 (2026-03-21) - MINOR: Abstract-zone tariff support (PLZ legend → zone mapping)
- ADDED: tariff_type field: "plz_as_zone" | "abstract_zones"
- ADDED: Abstract-zone handling for carriers that group PLZ prefixes into Zone 1–N
  columns with a separate PLZ lookup table (e.g. Kelbasha, many national LTL carriers)
- CHANGED: zones[] now allows range/list notation in plz_prefix (e.g. "80-81",
  "07-09, 36") — Python expands these into individual tariff_zone_map rows
- UNCHANGED: Type A (plz_as_zone) behaviour is identical to v1.1.1

v1.1.1 (2026-03-20) - PATCH: Per-km detection heuristic for Hauptlauf
- ADDED: Numeric reasoning rule: if Hauptlauf rate value < 5.00, classify as per-km
- Rationale: German industry convention — per-shipment and per-kg rates are always ≥5.00;
  sub-5.00 values in open-ended bands are per-km even without explicit unit in document
- Quality impact: FIXED — extractor was misclassifying low per-km rates as per-kg

v1.1.0 (2026-03-20) - MINOR: Three-section tariff model
- ADDED: Three-section model: Direkt (zone=-1), Vor-/Nachlauf (zone>0), Hauptlauf (zone=0)
- CHANGED: Hauptlauf: rate_per_kg field repurposed to store per-km rate
- ADDED: Direktverkehr: zone=-1 for direct-service rates if present in tariff

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
  German Hauptlauf flat rates for lighter shipments are typically EUR 200–800 per shipment.
  If the open-ended heavy band shows a value < 5.00, it is a per-km rate by convention.
  Store it in rate_per_kg and set rate_per_shipment = null.

━━━ STEP 3 — zones[] RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  TYPE A:  Emit one entry per rate-table column.
           zone integer = PLZ prefix as integer.
           plz_prefix = the column header string (e.g. "35").
           Example: {{"plz_prefix": "35", "zone": 35}}

  TYPE B:  Read the PLZ mapping table. For each (PLZ group, zone) entry in the table,
           emit one zones[] item. Use the exact text from the mapping table as plz_prefix
           — ranges and comma-separated lists are fine; Python will expand them.
           Example entries from a legend "Zone 1: 80-81, 85-86, 89":
             {{"plz_prefix": "80-81", "zone": 1}}
             {{"plz_prefix": "85-86", "zone": 1}}
             {{"plz_prefix": "89",    "zone": 1}}
           Include ALL zones and ALL PLZ groups from the legend.
           Do NOT add zones[] entries for zone 0 or zone -1.

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
  "confidence": 0.0,
  "issues": []
}}

━━━ ADDITIONAL RULES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- rates[]: one entry per (zone, weight band) cell for ALL three sections.
  weight_from_kg is the exclusive lower bound (row above); weight_to_kg is the inclusive upper.
  First band (e.g. "< 300 kg"): weight_from_kg = 0, weight_to_kg = 300.
  Open-ended heavy band ("> 1500 kg"): weight_from_kg = 1500, weight_to_kg = 99999.
  A "Mindestbetrag" / minimum charge row is NOT a weight band — skip it.
- valid_from: look for "ab 01.04.2022", "gültig ab", "neue Preise ab", "Version MM.YYYY".
  Convert German dates (dd.mm.yyyy) to ISO (YYYY-MM-DD).
  For "Version 01.2022" interpret as 2022-01-01.
- carrier_name: the company issuing the tariff (sender / footer company name).
- customer_name: the recipient company named on the cover letter or header (if any).
- currency: usually EUR; extract from document if present.
- confidence: your confidence in the extraction quality (0.0–1.0).
- issues: list any data quality problems or unclear values.

Document text:
{text}"""
