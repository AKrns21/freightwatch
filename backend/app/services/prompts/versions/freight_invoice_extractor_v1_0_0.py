"""
FreightWatch — freight_invoice_extractor Prompt — Version 1.0.0

Extracts structured shipment data from carrier invoice text
(text-mode PDFs that did not match a parsing template).
"""

VERSION = "v1.0.0"

CHANGELOG = """
v1.0.0 (2026-03-20) - Initial version
- Extracted from inline constants in invoice_parser.py
- JSON schema: header (invoice_number, invoice_date, carrier_name,
  customer_name, customer_number, total_amount, currency) +
  lines[] (shipment_date, shipment_reference, billing_type, tour_number,
  origin_zip, origin_country, dest_zip, dest_country, weight_kg,
  base_amount, line_total) + confidence + issues[]
- Rules: German date conversion, EU number format, PLZ extraction from addresses
- Model: claude-haiku-4-5-20251001
"""

SYSTEM_PROMPT = (
    "You are a precise data extraction engine for German freight carrier invoices. "
    "Your output MUST be a single valid JSON object — no markdown, no explanation, no code fences."
)

PROMPT_TEMPLATE = """\
Extract all shipment data from this freight invoice text.

Return a JSON object with this exact structure:
{{
  "header": {{
    "invoice_number": "string or null",
    "invoice_date": "YYYY-MM-DD or null",
    "carrier_name": "string or null",
    "customer_name": "string or null",
    "customer_number": "string or null",
    "total_amount": number or null,
    "currency": "EUR|CHF|USD|GBP or null"
  }},
  "lines": [
    {{
      "shipment_date": "YYYY-MM-DD or null",
      "shipment_reference": "string or null",
      "billing_type": "string or null",
      "tour_number": "string or null",
      "origin_zip": "5-digit postal code or null",
      "origin_country": "2-letter ISO code or null",
      "dest_zip": "5-digit postal code or null",
      "dest_country": "2-letter ISO code or null",
      "weight_kg": number or null,
      "base_amount": number or null,
      "line_total": number or null
    }}
  ],
  "confidence": 0.0,
  "issues": ["list of data quality problems or empty array"]
}}

Rules:
- Convert German dates (dd.mm.yy / dd.mm.yyyy) to YYYY-MM-DD format
- Remove thousand separators; use period as decimal separator (1.234,56 → 1234.56)
- Extract 5-digit PLZ from full addresses (e.g. "D-42551 Velbert" → "42551")
- One JSON object per shipment line; skip VAT summary rows and invoice total rows
- Set confidence between 0.0 and 1.0 based on field completeness and text clarity
- If a field is absent or illegible: use null

Document text:
{text}"""
