---
name: Parsing templates — design decisions
description: How global parsing templates work and why they are generic, not carrier-specific
type: project
---

Global templates (tenant_id IS NULL) must NEVER be carrier-specific. There are hundreds of different carrier invoice formats.

**Current global templates (seeded via backend/app/scripts/seed_templates.py):**
1. Sendungsliste – Deutsch (CSV) — detection keyword: "datum", mime: text/csv
2. Freight List – English (CSV) — detection keyword: "date", mime: text/csv
3. Sendungsliste – Deutsch (XLSX) — detection keyword: "datum", mime: spreadsheet/excel
4. Freight List – English (XLSX) — detection keyword: "date", mime: spreadsheet/excel

**Matching logic:**
- Single keyword in detection + MIME type = 80% confidence (above 70% threshold)
- "datum" is substring of Versanddatum, Lieferdatum, Rechnungsdatum, etc.
- "date" is substring of shipment_date, delivery_date, invoice_date, etc.

**Mappings use {"keywords": [...]} with exhaustive field aliases** — not hardcoded column names.

**Re-seed after editing:** cd backend && python -m app.scripts.seed_templates (idempotent, updates in place)

**Why:** CLAUDE.md has a "Parsing Templates" section documenting this design.
