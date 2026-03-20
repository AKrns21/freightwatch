"""
FreightWatch — tariff_extractor Prompt — Version 1.1.1

Extracts structured rate-table data from carrier tariff PDFs
(price lists, Tarifblatt, Preisliste, Konditionenblatt).
"""

VERSION = "v1.1.1"

CHANGELOG = """
v1.1.1 (2026-03-20) - PATCH: Per-km detection heuristic for Hauptlauf
- ADDED: Numeric reasoning rule: if Hauptlauf rate value < 5.00, classify as per-km
- Rationale: German industry convention — per-shipment and per-kg rates are always ≥5.00;
  sub-5.00 values in open-ended bands are per-km even without explicit unit in document
- Quality impact: FIXED — extractor was misclassifying low per-km rates as per-kg

v1.1.0 (2026-03-20) - MINOR: Three-section tariff model
- ADDED: Three-section model: Direkt (zone=-1), Vor-/Nachlauf (zone>0), Hauptlauf (zone=0)
- CHANGED: Hauptlauf: rate_per_kg field repurposed to store per-km rate (km pricing is the standard for trunk haul)
- ADDED: Direktverkehr: zone=-1 for direct-service rates if present in tariff
- Rationale: Single flat-zone model could not represent carrier tariffs with separate trunk-haul pricing

v1.0.0 (2026-03-20) - Initial version
- Handles German domestic zone-based tariffs with PLZ-prefix zone columns
- Weight band rows: <300, <400, ..., >1500 kg (per-shipment flat rate or per-kg)
- Hauptlauf / trunk-haul section: flat rates for heavy shipments (zone=0)
- Extracts carrier name, customer name, valid_from date, currency, lane_type
- JSON schema: carrier_name, customer_name, valid_from, currency, lane_type,
  zones[], rates[], confidence, issues[]
- Model: claude-haiku-4-5-20251001
"""

SYSTEM_PROMPT = (
    "You are a precise data extraction engine for German freight carrier tariff sheets. "
    "Your output MUST be a single valid JSON object — no markdown, no explanation, no code fences."
)

PROMPT_TEMPLATE = """\
Extract the rate table from this freight carrier tariff document.

German LTL tariffs have up to three sections — extract all that are present:

  Vor-/Nachlauf  (zone > 0)  — local pickup / delivery, priced by PLZ zone × weight band.
                               Use the PLZ prefix as the integer zone number.
  Hauptlauf      (zone = 0)  — trunk-haul transport between hubs.
                               Lighter bands: flat rate per shipment (rate_per_shipment).
                               Heavy / open-ended band: per-km rate stored in rate_per_kg.
  Direktverkehr  (zone = -1) — direct-service rates, if present. Use zone = -1.

Per-km detection rule for Hauptlauf (IMPORTANT):
  German Hauptlauf flat rates for lighter shipments are typically EUR 200–800 per shipment.
  For the open-ended heavy band (e.g. "> 1500 kg"), the document sometimes shows only a
  small number such as 0.1100 or 0.0950 without stating the unit.
  Rule: if the numeric value is < 5.00 it CANNOT be per-shipment or per-kg —
  it is a per-km rate by German freight industry convention.
  Store it in rate_per_kg and set rate_per_shipment = null.
  Apply this reasoning even when "km" or "pro km" is not written in the document.

Return a JSON object with this exact structure:
{{
  "carrier_name": "string or null",
  "customer_name": "string or null",
  "valid_from": "YYYY-MM-DD or null",
  "currency": "EUR or other 3-letter code",
  "lane_type": "domestic_de",
  "zones": [
    {{"plz_prefix": "35", "zone": 35}},
    ...
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

Rules:
- zones[]: Vor-/Nachlauf only (zone > 0). Each column in the rate table is a PLZ prefix.
  Use the PLZ prefix value as the integer zone number.
  Do NOT add entries for zone 0 or zone -1 here.
- rates[]: one entry per (zone, weight band) cell for ALL three sections.
  weight_from_kg is the exclusive lower bound (row above); weight_to_kg is the inclusive upper.
  First band (e.g. "< 300 kg"): weight_from_kg = 0, weight_to_kg = 300.
  Open-ended heavy band ("> 1500 kg"): weight_from_kg = 1500, weight_to_kg = 99999.
- valid_from: look for "ab 01.04.2022", "gültig ab", "neue Preise ab".
  Convert German dates (dd.mm.yyyy) to ISO (YYYY-MM-DD).
- carrier_name: the company issuing the tariff (sender of the letter).
- customer_name: the recipient company named on the cover letter (if any).
- currency: usually EUR; extract from document if present.
- confidence: your confidence in the extraction quality (0.0–1.0).
- issues: list any data quality problems or unclear values.

Document text:
{text}"""
