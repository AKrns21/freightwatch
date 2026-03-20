"""
FreightWatch — tariff_extractor Prompt — Version 1.1.1

Extracts structured rate-table data from carrier tariff PDFs
(price lists, Tarifblatt, Preisliste, Konditionenblatt).
"""

VERSION = "v1.1.1"

CHANGELOG = """
v1.1.1 (2026-03-20)
- Explicit numeric reasoning rule for per-km detection in Hauptlauf open-ended bands:
  if the rate value is < 5.00 it cannot be per-shipment or per-kg — it is per-km by
  German industry convention, even when the document does not state the unit.

v1.1.0 (2026-03-20)
- Three-section model: Direkt (zone=-1), Vor-/Nachlauf (zone>0), Hauptlauf (zone=0)
- Hauptlauf: rate_per_kg stores per-km rate (km pricing standard for trunk haul)
- Direktverkehr: zone=-1 for direct-service rates if present

v1.0.0 (2026-03-20) - Initial version
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
