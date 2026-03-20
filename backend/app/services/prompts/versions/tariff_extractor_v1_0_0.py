"""
FreightWatch — tariff_extractor Prompt — Version 1.0.0

Extracts structured rate-table data from carrier tariff PDFs
(price lists, Tarifblatt, Preisliste, Konditionenblatt).
"""

VERSION = "v1.0.0"

CHANGELOG = """
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
    ...
  ],
  "confidence": 0.0,
  "issues": []
}}

Rules:
- zones[]: each column in the rate table is a PLZ prefix (e.g. "35", "60").
  Use the PLZ prefix value as the integer zone number.
- rates[]: one entry per (zone, weight band) cell.
  weight_from_kg is the exclusive lower bound (row above); weight_to_kg is the inclusive upper.
  For the first weight band (e.g. "< 300 kg"): weight_from_kg = 0, weight_to_kg = 300.
  For "> 1500 kg" or similar open-ended rows: weight_from_kg = 1500, weight_to_kg = 99999,
  rate_per_shipment = null, rate_per_kg = <per-kg rate>.
- Hauptlauf / trunk haul section (if present): flat rates for heavier shipments
  with no zone dependency. Use zone = 0 for these entries.
  Example: "< 4000 kg = 530.00 EUR" → zone=0, weight_from_kg=0, weight_to_kg=4000,
  rate_per_shipment=530.00, rate_per_kg=null.
- valid_from: look for dates like "ab 01.04.2022", "gültig ab", "neue Preise ab".
  Convert German date format (dd.mm.yyyy) to ISO (YYYY-MM-DD).
- carrier_name: the company issuing the tariff (sender of the letter).
- customer_name: the recipient company named on the cover letter (if any).
- currency: usually EUR; extract from document if present.
- confidence: your confidence in the extraction quality (0.0–1.0).
- issues: list any data quality problems or unclear values.

Document text:
{text}"""
