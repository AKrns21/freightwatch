"""
FreightWatch — freight_invoice_extractor Prompt — Version 1.2.1

Extracts structured shipment data from carrier invoice text
(text-mode PDFs that did not match a parsing template).
"""

VERSION = "v1.2.1"

CHANGELOG = """
v1.2.1 (2026-03-20) - PATCH: total_amount net/gross clarification
- FIXED: total_amount explicitly net amount (Nettobetrag, excl. MwSt/VAT), not gross/Brutto total
- Rationale: Invoices with MwSt rows caused extractor to pick up Brutto total at end of page
- Quality impact: CRITICAL FIX — total_amount was overstated by 19% on affected invoices

v1.2.0 (2026-03-20) - MINOR: Skip rule clarification for multi-page invoices
- FIXED: Vollständigkeit-header pages are regular invoice pages, not skip targets — extract all shipment rows
- FIXED: Page-level annotations (Seite X von Y, Vollständigkeit, etc.) are headers only, not rows to skip
- Rationale: Extractor was dropping valid shipment rows on multi-page Mecu-style invoices
- Quality impact: IMPROVED — shipment row completeness on paginated invoices

v1.1.0 (2026-03-20) - MINOR: Multi-invoice support + date precision
- ADDED: invoice_number per line item (supports multi-invoice documents)
- CHANGED: shipment_reference: capture ALL reference numbers/identifiers, comma-separated
- ADDED: Explicit rule: read every digit of dates carefully, do not guess ambiguous digits
- FIXED: issues[]: only genuine data problems, not observations about document structure

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
    "total_amount": "NET amount excl. MwSt/VAT (Nettobetrag) — number or null",
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
      "base_amount": "net freight base charge excl. surcharges — number or null",
      "line_total": "net line total excl. MwSt/VAT — number or null"
    }}
  ],
  "confidence": 0.0,
  "issues": ["genuine data quality problems only — omit structural observations"]
}}

Rules:
- Convert German dates (dd.mm.yy / dd.mm.yyyy) to YYYY-MM-DD; read every digit carefully
- Remove thousand separators; use period as decimal separator (1.234,56 → 1234.56)
- Extract 5-digit PLZ from full addresses (e.g. "D-42551 Velbert" → "42551")
- One line object per shipment row
- ONLY skip rows that are VAT/MwSt totals, invoice subtotals, or payment summary lines
- Page headers such as "Seite X von Y", "Vollständigkeit = XX%", or any other page-level \
annotation are NOT rows — ignore the annotation itself but extract all shipment rows on that page
- total_amount and line_total: always use NET amounts (Nettobetrag, excl. MwSt) — \
never the Brutto/gross total that includes VAT
- shipment_reference: include ALL reference fields visible for that row (Referenz, Beleg-Nr., \
Auftrags-Nr., barcode, etc.), comma-separated
- invoice_number per line: use the invoice header that governs this line item
- Set confidence between 0.0 and 1.0 based on field completeness and text clarity
- If a field is absent or illegible: use null

Document text:
{text}"""
