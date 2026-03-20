"""
FreightWatch — freight_invoice_extractor Prompt — Version 1.1.0

Extracts structured shipment data from carrier invoice text
(text-mode PDFs that did not match a parsing template).
"""

VERSION = "v1.1.0"

CHANGELOG = """
v1.1.0 (2026-03-20)
- Added invoice_number per line item (supports multi-invoice documents)
- shipment_reference: capture ALL reference numbers/identifiers, comma-separated
- Explicit rule: read every digit of dates carefully, do not guess ambiguous digits
- issues[]: only genuine data problems, not observations about document structure
"""

SYSTEM_PROMPT = (
    "You are a precise data extraction engine for German freight carrier invoices. "
    "Your output MUST be a single valid JSON object — no markdown, no explanation, no code fences."
)

PROMPT_TEMPLATE = """\
Extract all shipment line items from this freight invoice text. \
The document may contain multiple invoices — assign the correct invoice number to each line.

Return a JSON object with this exact structure:
{{
  "header": {{
    "invoice_number": "primary or first invoice number, string or null",
    "invoice_date": "YYYY-MM-DD or null",
    "carrier_name": "string or null",
    "customer_name": "string or null",
    "customer_number": "string or null",
    "total_amount": number or null,
    "currency": "EUR|CHF|USD|GBP or null"
  }},
  "lines": [
    {{
      "invoice_number": "invoice number this line belongs to, string or null",
      "shipment_date": "YYYY-MM-DD or null",
      "shipment_reference": "ALL reference numbers and identifiers for this shipment, comma-separated if multiple",
      "billing_type": "string or null",
      "tour_number": "Auftrag or tour number, string or null",
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
  "issues": ["genuine data quality problems only — omit structural observations"]
}}

Rules:
- Convert German dates (dd.mm.yy / dd.mm.yyyy) to YYYY-MM-DD; read every digit carefully
- Remove thousand separators; use period as decimal separator (1.234,56 → 1234.56)
- Extract 5-digit PLZ from full addresses (e.g. "D-42551 Velbert" → "42551")
- One line object per shipment row; skip VAT summary rows and invoice total rows
- shipment_reference: include ALL reference fields visible for that row (Referenz, Beleg-Nr., \
Auftrags-Nr., barcode, etc.), comma-separated
- invoice_number per line: use the invoice header that governs this line item
- Set confidence between 0.0 and 1.0 based on field completeness and text clarity
- If a field is absent or illegible: use null

Document text:
{text}"""
