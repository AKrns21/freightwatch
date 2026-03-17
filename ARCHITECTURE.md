# FreightWatch — Platform Architecture

> SaaS platform for a **logistics consultancy** that analyses freight costs on behalf of
> multiple **client companies** (Mittelstand). Each client is a tenant. The consultancy
> configures the system; clients may optionally get read-only access to their reports.

**Last revised:** 2026-03-17 (updated with clarifications)
**Stack:** NestJS · TypeORM · PostgreSQL/Supabase · Redis/Bull · React 19 · Anthropic Claude

---

## 1. Goals and Scope

| Business Question | Module |
|---|---|
| "Does the XYZ invoice match the agreed tariff?" | Invoice Verification |
| "What does my own delivery cost per stop vs. a carrier?" | Own vs. Carrier Benchmark |
| "Where are outliers?" — delta analysis, cross-carrier benchmark | Outlier & Benchmark Analysis |

The platform intentionally does **not** know in advance which questions will be asked next.
The design principle is: **extract everything, analyse lazily.** Data quality and completeness
in the database is the primary concern; analysis modules are secondary and can be added later.

### 1.1 Operator Model

```
Logistics Consultancy (platform operator)
  └── Client A (tenant) — 1–5 Mio EUR freight/year ≈ 1,000–4,000 shipments/month
  └── Client B (tenant) — different carriers, different tariffs
  └── Client C (tenant) — may or may not operate own delivery fleet
```

The consultancy uploads documents on behalf of clients. Each client is isolated via
PostgreSQL Row Level Security (RLS). Future: clients can log in to view their own reports.

### 1.2 Volume Baseline

| Metric | Estimate | Notes |
|---|---|---|
| Shipments per client per month | 1,000 – 4,000 | At ~100 EUR avg, 1–5 Mio EUR/year |
| Clients | 10 – 50 (at scale) | Each is one tenant |
| Invoice lines per month (platform total) | 50,000 – 200,000 | Well within PostgreSQL range |
| Active tariffs per client | 2 – 10 | Multiple carriers, may overlap in time |

PostgreSQL with standard B-tree indexes on `(tenant_id, carrier_id, shipment_date)` is
sufficient for this volume. No partitioning or analytical database needed.

---

## 2. Document Taxonomy

Four document classes are ingested. Each maps to a specific set of DB entities.

### 2.1 Tariff Sheets (Tarifblätter)

**Source formats:** XLSX (primary), PDF (text or scanned), CSV

**What to extract:**

```
Carrier metadata
  - carrier_name, carrier_address
  - customer_name, customer_address
  - valid_from, valid_until
  - service_type (Stückgut, Ladung, Express …)
  - lane_scope (domestic_de, de_to_at, de_to_ch …)

Zone definitions
  - For each zone: zone_number, label, plz_description (raw text)
  - Expanded PLZ-to-zone map (prefix + exact matches)

Rate matrix
  - weight_from_kg, weight_to_kg, price per zone
  - NULL entries where no rate exists for that weight band

Surcharges (Nebenkosten)
  - diesel_floater_pct (float, at time of issue)
  - toll_matrix (weight_band × distance_range → EUR)
  - One-off fees: pallet_exchange, manual_order_fee, avis_fee …
  - Minimum weights per shipment unit type
  - payment_days, billing_cycle
  - legal_basis (ADSp version)
```

**Target tables:** `tariff_table`, `tariff_rate`, `tariff_zone_map`, `diesel_floater`,
`tariff_nebenkosten`, `tariff_surcharge`, `tariff_special_condition` (see §4)

#### Three Pricing Types

| Type | German term | Pricing basis | Rate table |
|---|---|---|---|
| LTL | Stückgut | Zone × weight band → fixed price | `tariff_rate` |
| FTL | Ladung / Charter | per km, per day, or flat tour | `tariff_ftl_rate` |
| Agreement | Vereinbarungspreis | Flat per tour, regardless of zone or weight | `tariff_special_condition` (condition_type=`'flat_tour'`) or separate `billing_type` |

The third type — **Vereinbarungspreise** — is common in practice. Example: carrier AS Stahl
bills `billing_type=200` at EUR 530/555 flat for a full tour to PLZ 61118, regardless of
actual weight. This is neither LTL zone/weight logic nor a distance-based FTL rate.

**Modelling decision**: Vereinbarungspreise are handled as `tariff_special_condition` with
`condition_type = 'flat_tour'` and `dest_zip_prefix` narrowing the scope. This avoids a
fourth rate table while keeping the logic in the "check special conditions first" step.

All three use the same `tariff_table` header. The `service_type` field determines which
lookup applies. `billing_type` on `invoice_line` is used to classify incoming invoice lines.

#### Surcharge Storage: Dual Approach

Two complementary tables store surcharge/Nebenkosten data:

| Table | Purpose | Structure |
|---|---|---|
| `tariff_nebenkosten` | Known, typed surcharges with queryable fields | Strongly typed columns: `diesel_floater_pct`, `maut_basis`, `min_weight_*`, `pallet_exchange_*`, `avis_fee` etc. |
| `tariff_surcharge` | Flexible catch-all for unknown or ad-hoc surcharges | `surcharge_type text`, `basis text`, `value numeric` — no schema change needed for new types |

Benchmark calculation uses `tariff_nebenkosten` for the core calculation (diesel, maut, Mindestgewicht)
and `tariff_surcharge` for anything additional. New carrier-specific fees go into
`tariff_surcharge` without requiring a migration.

---

### 2.2 Invoices (Rechnungen / Frachtrechnung)

**Source formats:** PDF (digital or scanned), JPG/PNG, occasionally DOCX

**What to extract:**

```
Invoice header
  - invoice_number, invoice_date
  - carrier_name, customer_name, customer_number
  - total_net_amount, total_gross_amount, currency
  - vat_pct (if present)
  - billing_period (if stated)

Per line item
  - shipment_reference (Sendungsnummer / Beleg-Nr.)
  - shipment_date
  - tour (Tournummer, if present)
  - origin_zip, origin_country
  - dest_zip, dest_country
  - weight_kg (chargeable weight as invoiced)
  - unit_price, line_total
  - billing_type (carrier-specific code, e.g. "200"=Vollladung, "201"=Stückgut)
  - surcharge_lines (diesel, maut, avis, etc. if broken out)

Extraction metadata
  - confidence (0–1, LLM-assigned)
  - issues[] (list of parse warnings)
  - source_file, extracted_at
```

**Target tables:** `invoice_header`, `invoice_line`

---

### 2.3 Shipment Data (Sendungsdaten)

**Source formats:** CSV, XLSX (carrier exports, own TMS/WMS/ERP exports)

**What to extract:**

```
Per shipment
  - shipment_reference (must match invoice line for reconciliation)
  - shipment_date
  - carrier_code / carrier_name
  - service_level (Express, Standard, Economy …)
  - origin_zip, origin_city, origin_country
  - dest_zip, dest_city, dest_country
  - weight_actual_kg
  - weight_volumetric_kg  (if dimensions available)
  - ldm (loading metres, if applicable)
  - package_count
  - declared_value (for insurance purposes)
  - customer_order_reference (from own ERP)
  - cost_paid (if available from ERP)
```

**Target table:** `shipment`

The `shipment_reference` is the join key between shipment records and invoice lines.
Reconciliation rate (how many invoice lines can be matched to a shipment) is a quality KPI.

---

### 2.4 Own Tour Data (Eigene Touren / Zustelltouren) *(optional, client-dependent)*

**Availability:** Only relevant for clients that operate their own delivery fleet.
Typically exported as CSV from dispatching software (PTV, HERE, Fleetboard, or custom Excel).
The import schema must be flexible — column names vary by system.

**Source formats:** CSV (primary), XLSX

**What to extract:**

```
Per tour
  - tour_id, tour_date
  - vehicle_id (link to fleet_vehicle)
  - driver_id (link to fleet_driver)
  - departure_zip, depot_zip
  - distance_km_total
  - duration_hours

Per stop within a tour
  - stop_sequence
  - dest_zip, dest_city
  - shipment_reference (link to shipment)
  - arrival_time, departure_time
  - packages_delivered, weight_kg
```

**Target tables:** `own_tour`, `own_tour_stop`, `fleet_vehicle`, `fleet_driver`
(see §6.2 — Own vs. Carrier Benchmark Module)

---

## 3. Ingestion Pipeline

```
┌─────────────┐   upload   ┌──────────────────┐   queue   ┌──────────────────────┐
│  User / API │ ──────────▶│  UploadService   │ ─────────▶│  UploadProcessor     │
│             │            │  - SHA256 hash   │           │  (Bull worker)       │
└─────────────┘            │  - dedup check   │           └──────────┬───────────┘
                           │  - store buffer  │                      │
                           └──────────────────┘                      │ classify document type
                                                                      │
                                        ┌─────────────────────────────┼────────────────────────┐
                                        │                             │                        │
                                        ▼                             ▼                        ▼
                              ┌──────────────────┐       ┌──────────────────┐     ┌──────────────────┐
                              │ TariffParser     │       │ InvoiceParser    │     │ CsvParser        │
                              │ (xlsx/pdf)       │       │ (pdf/jpg/docx)   │     │ (shipment data)  │
                              └────────┬─────────┘       └────────┬─────────┘     └────────┬─────────┘
                                       │                           │                        │
                                       ▼                           ▼                        ▼
                              ┌──────────────────────────────────────────────────────────────────────┐
                              │                  ExtractionValidator                                 │
                              │  - Schema validation (required fields present)                       │
                              │  - Cross-check: sum(line_totals) ≈ invoice.total_net ±0.02 EUR       │
                              │  - Duplicate detection (same invoice_number + carrier)               │
                              │  - Confidence threshold: flag if < 0.75 for human review            │
                              └───────────────────────────────────┬──────────────────────────────────┘
                                                                  │
                                           ┌──────────────────────┼──────────────────────┐
                                           │                      │                      │
                                           ▼                      ▼                      ▼
                                    PASS (auto-import)    LOW CONFIDENCE          FAIL (rejected)
                                           │              (flag for review)               │
                                           ▼                      │                      ▼
                              ┌──────────────────┐                │           ┌──────────────────┐
                              │  DB Import       │◀───────────────┘           │  upload.status   │
                              │  (write entities)│   after human approval     │  = 'failed'      │
                              └──────────┬───────┘                            │  + error detail  │
                                         │                                    └──────────────────┘
                                         ▼
                              ┌──────────────────────┐
                              │  BenchmarkEngine     │
                              │  (async, post-import)│
                              │  - run for shipments │
                              │  - write to          │
                              │    shipment_benchmark│
                              └──────────────────────┘
```

### 3.1 Document Type Detection

The pipeline must classify the uploaded document before routing to the correct parser.
Classification is based on (in order):

1. **File extension** — `.xlsx` / `.csv` → always structured data
2. **Filename heuristics** — "Tarif", "Entgelte", "Preisliste" → tariff; "Rechnung", "RG" → invoice
3. **LLM classification** — for ambiguous PDFs/JPGs: send first page text or thumbnail to Claude,
   ask for document type (tariff / invoice / shipment-export / other)
4. **User override** — upload form always allows manual type selection

### 3.2 Vision Parsing Strategy (Scanned Documents)

Current state: single Claude Vision call with all pages.

**Target state — multi-stage pipeline:**

```
Stage 1: Pre-processing (sharp / jimp)
  - Deskew (detect rotation angle, correct)
  - Binarize (high-contrast B&W for dense number tables)
  - Increase resolution to min 200 DPI before encoding

Stage 2: Page classification
  - Classify each page: "cover", "line-item-table", "surcharge-appendix", "continuation"
  - Skip cover pages after extracting header data

Stage 3: Structured extraction (Claude Vision, per page or batched)
  - System prompt: carrier-specific if known (pass expected field names as hints)
  - Request JSON schema output (strict mode)
  - For tables: extract as array-of-rows, NOT as prose summary

Stage 4: Cross-document validation (Python/TypeScript)
  - Sum of extracted line totals MUST equal header total ± 0.02 EUR
  - All required fields must be non-null
  - Date plausibility (invoice_date within last 3 years)
  - Weight sanity (0 < weight_kg < 30,000)

Stage 5: Confidence scoring (per field, not per document)
  - Mark each extracted field with extraction_source: "direct_ocr" | "llm_inferred" | "missing"
  - Flag fields with extraction_source = "llm_inferred" in the review UI
  - Document-level confidence = f(% of required fields with direct_ocr)

Stage 6: Human review (if confidence < threshold or validation fails)
  - Show original PDF alongside extracted JSON
  - Allow field-by-field correction
  - Store corrections in extraction_corrections table (for future prompt improvement)
```

**Threshold guidance:**

| Confidence | Action |
|---|---|
| ≥ 0.90 | Auto-import |
| 0.75–0.89 | Auto-import + flag for spot check |
| 0.50–0.74 | Hold for human review |
| < 0.50 | Reject, notify user |

---

## 4. Database Schema

### 4.1 Entity Map

```
tenant ─────────────────────────────────────────────────────────────────────────┐
  │                                                                              │
  ├── project ──────────────────────────────────────────────────────┐            │
  │     └── upload (file, status, type)                             │            │
  │           ├── shipment ──────────────────────────────┐          │            │
  │           │     └── shipment_benchmark ─────────────┤          │            │
  │           ├── invoice_header ──────────────────────┐ │          │            │
  │           │     └── invoice_line ─────────────────-┤ │          │            │
  │           └── tariff_table ─────────────────────┐  │ │          │            │
  │                 ├── tariff_rate                  │  │ │          │            │
  │                 ├── tariff_zone_map              │  │ │          │            │
  │                 └── tariff_surcharge             │  │ │          │            │
  │                                                  │  │ │          │            │
  ├── carrier (global) ──────────────────────────────┘  │ │          │            │
  │     └── carrier_alias (tenant-scoped)                │ │          │            │
  │                                                      │ │          │            │
  ├── diesel_floater ────────────────────────────────────┘ │          │            │
  ├── fx_rate (global) ──────────────────────────────────┘          │            │
  │                                                                   │            │
  ├── own_tour ──────────────────────────────────────────────────────┘            │
  │     ├── own_tour_stop                                                          │
  │     └── fleet_vehicle ──────────────────────────────────────────────────────-┘
  │           └── fleet_driver
  │
  └── extraction_correction (audit trail of human corrections)
```

### 4.2 Core Tables (existing + additions)

```sql
-- ─────────────────── TARIFF SURCHARGES (new) ──────────────────────────────
-- Currently surcharges are embedded in tariff_table metadata.
-- Moving to dedicated table makes them queryable.
CREATE TABLE tariff_surcharge (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_id     uuid NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  tenant_id     uuid NOT NULL,
  surcharge_type text NOT NULL,  -- 'diesel_floater' | 'avis' | 'manual_order' | 'pallet_exchange' | ...
  basis         text,            -- 'per_shipment' | 'pct_of_base' | 'flat'
  value         numeric(12,4),   -- EUR or %
  currency      char(3) DEFAULT 'EUR',
  notes         text
);
ALTER TABLE tariff_surcharge ENABLE ROW LEVEL SECURITY;


-- ─────────────────── SPECIAL CONDITIONS (Sonderkonditionen) ──────────────
-- Individual client agreements that override the standard tariff matrix.
-- Examples: fixed flat rate for a specific PLZ, negotiated cap for heavy shipments.
-- Checked BEFORE the standard tariff matrix — if a match is found, it takes priority.
CREATE TABLE tariff_special_condition (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_id       uuid NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL,
  condition_type  text NOT NULL,   -- 'fixed_price' | 'price_cap' | 'min_price' | 'pct_discount'
  -- Scope: which shipments this condition applies to (all NULL = applies to all)
  dest_zip_prefix text,            -- e.g. '61118' exact or '61' prefix
  weight_from_kg  numeric(10,2),
  weight_to_kg    numeric(10,2),
  -- The override value
  value           numeric(12,4) NOT NULL,  -- EUR (for fixed/cap/min) or % (for discount)
  description     text,            -- free text, e.g. "Sonderpreis Kunde Mecu Zone 8"
  valid_from      date NOT NULL,
  valid_until     date,
  CONSTRAINT tariff_special_condition_tenant_fk CHECK (tenant_id IS NOT NULL)
);
ALTER TABLE tariff_special_condition ENABLE ROW LEVEL SECURITY;


-- ─────────────────── FTL / CHARTER RATES (new) ────────────────────────────
-- Flat rates for full-truckload and charter shipments (no zone/weight matrix).
CREATE TABLE tariff_ftl_rate (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tariff_id       uuid NOT NULL REFERENCES tariff_table(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL,
  rate_basis      text NOT NULL,   -- 'per_km' | 'per_day' | 'flat_tour'
  vehicle_type    text,            -- '7.5t' | '12t' | '40t' | NULL = all
  dest_region     text,            -- optional PLZ prefix or free text region label
  price           numeric(12,4) NOT NULL,
  currency        char(3) DEFAULT 'EUR',
  min_price       numeric(12,4),   -- minimum charge per tour
  notes           text
);
ALTER TABLE tariff_ftl_rate ENABLE ROW LEVEL SECURITY;


-- ─────────────────── EXTRACTION CORRECTIONS (new) ─────────────────────────
-- Store human corrections to improve future OCR/LLM extractions
CREATE TABLE extraction_correction (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  upload_id       uuid NOT NULL REFERENCES upload(id),
  field_path      text NOT NULL,   -- JSON path, e.g. "lines[3].weight_kg"
  original_value  text,
  corrected_value text NOT NULL,
  corrected_by    uuid,            -- user_id
  corrected_at    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE extraction_correction ENABLE ROW LEVEL SECURITY;


-- ─────────────────── OWN FLEET (new, optional module) ─────────────────────
CREATE TABLE fleet_vehicle (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  license_plate   text NOT NULL,
  vehicle_type    text,            -- 'sprinter' | '7.5t' | '12t' | '40t'
  payload_kg      int,
  fixed_cost_per_day  numeric(10,2),
  variable_cost_per_km numeric(8,4),
  currency        char(3) DEFAULT 'EUR',
  active          boolean DEFAULT true
);
ALTER TABLE fleet_vehicle ENABLE ROW LEVEL SECURITY;

CREATE TABLE fleet_driver (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  name            text NOT NULL,
  hourly_rate     numeric(8,2),
  currency        char(3) DEFAULT 'EUR',
  active          boolean DEFAULT true
);
ALTER TABLE fleet_driver ENABLE ROW LEVEL SECURITY;

CREATE TABLE own_tour (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  upload_id       uuid REFERENCES upload(id),  -- source file, if imported
  tour_id         text NOT NULL,               -- carrier's / dispatcher's tour number
  tour_date       date NOT NULL,
  vehicle_id      uuid REFERENCES fleet_vehicle(id),
  driver_id       uuid REFERENCES fleet_driver(id),
  depot_zip       char(5),
  distance_km     numeric(8,2),
  duration_hours  numeric(6,2),
  stop_count      int,
  total_weight_kg numeric(10,2),
  raw_data        jsonb            -- preserve full original row
);
ALTER TABLE own_tour ENABLE ROW LEVEL SECURITY;

CREATE TABLE own_tour_stop (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  tour_id         uuid NOT NULL REFERENCES own_tour(id) ON DELETE CASCADE,
  stop_sequence   int NOT NULL,
  dest_zip        char(5),
  dest_city       text,
  shipment_ref    text,            -- FK to shipment.shipment_reference (soft ref)
  weight_kg       numeric(10,2),
  packages        int,
  arrival_time    timestamptz,
  departure_time  timestamptz
);
ALTER TABLE own_tour_stop ENABLE ROW LEVEL SECURITY;
```

### 4.3 Migration Workflow

```
backend/src/database/migrations/
  001_fresh_schema.sql         ← baseline (2026-03-16)
  002_upload_status_enum.sql
  003_invoice_line_validation_constraint.sql
  004_tariff_table_parsing_metadata.sql
  005_upload_missing_columns.sql
  006_tariff_surcharge_table.sql          ← next
  007_tariff_special_conditions.sql       -- Sonderkonditionen
  008_tariff_ftl_rates.sql                -- FTL/Charter rates
  009_tariff_international_lanes.sql      -- dest_country_codes on tariff_table
  010_invoice_line_dispute_fields.sql     -- dispute_status, dispute_note
  011_invoice_dispute_events.sql          -- audit trail for disputes
  012_extraction_corrections.sql
  013_own_fleet_tables.sql
  014_tenant_delta_threshold.sql          -- freight_delta_threshold_pct on tenant
```

**Rules:**
- Every migration is a plain SQL file, numbered sequentially, never edited after merge
- TypeORM migration runner applies them in order: `npm run migration:run`
- Rollback: `npm run migration:revert` (each migration must have a `down` equivalent)
- New enum values: always add to existing enum type, never remove (backwards compat)
- RLS: every new tenant-scoped table gets `ENABLE ROW LEVEL SECURITY` + policy in same migration

---

## 5. Analysis Modules

Each module is an independent NestJS module. Modules read from core tables and write to their
own result tables. They do not modify core data.

### 5.1 Module 1: Invoice Verification (Rechnungsprüfung)

**Question:** "Does the XYZ invoice match the agreed tariff?"

#### Invoice Line Classification

Every invoice line falls into one of three categories. **All lines are always imported**,
because even non-standard positions are needed for total-cost comparisons across carriers.

| Type | Description | Benchmark treatment |
|---|---|---|
| `standard` | Weight-based LTL/Stückgut charge — matches zone × weight matrix | Full tariff benchmark |
| `vereinbarung` | Flat tour/agreement price (e.g. LA 200 at EUR 530) | Compared against `tariff_special_condition` flat_tour |
| `surcharge` | Diesel, toll, Avis, pallet exchange, customs etc. | Compared against `tariff_nebenkosten` / `tariff_surcharge` |
| `one_time` | Unstructured one-off positions (no tariff counterpart) | Stored as-is; in total invoice cost only |

The `billing_type` code from the invoice (e.g. `200`=Vereinbarung, `201`=Stückgut) is used
to determine `line_type`, with LLM fallback for unknown carrier codes. The mapping
`billing_type → line_type` is stored per carrier in `carrier.billing_type_map jsonb`.

#### Delta Threshold (configurable per tenant)

The flag threshold is stored in `tenant.freight_delta_threshold_pct` (default: 5.0).
Consultants can adjust this per client before running an analysis.

```sql
-- In tenant table (add column):
ALTER TABLE tenant ADD COLUMN freight_delta_threshold_pct numeric(5,2) DEFAULT 5.0;
```

#### Algorithm

```
For each invoice_line il:
  threshold = tenant.freight_delta_threshold_pct

  1. Find active tariff: tariff_table WHERE carrier = il.carrier
                                         AND lane_type matches il.origin/dest
                                         AND valid_from ≤ il.shipment_date
                                         AND (valid_until IS NULL OR valid_until ≥ il.shipment_date)

  If no tariff found:
    → status = 'no_tariff'
    → still store il in shipment_benchmark with actual amounts, delta = NULL
    → included in total invoice cost for cross-carrier comparison

  2. If il.line_type = 'vereinbarung':
     Look up tariff_special_condition WHERE condition_type = 'flat_tour'
                                        AND dest_zip_prefix matches il.dest_zip
                                        AND validity covers il.shipment_date
     expected_base = special_condition.value
     → skip to step 5

  3. Check Sonderkonditionen for standard lines (tariff_special_condition, non-flat_tour):
     IF match found (dest_zip, weight range, validity) THEN
       apply special condition (fixed_price / cap / discount)
       skip step 4, go to step 5
     END

  4. For LTL (service_type = 'stückgut'):
     Determine zone: tariff_zone_map (exact match before prefix match on dest_zip)
     Find weight band: tariff_rate WHERE zone = computed_zone
                                    AND weight_from_kg ≤ il.weight_kg ≤ weight_to_kg
     expected_base = tariff_rate.price

     For FTL (service_type = 'ftl' / 'charter'):
     Find rate: tariff_ftl_rate WHERE vehicle_type matches AND dest_region matches (if set)
     expected_base = price × distance_km  (for per_km)
                   OR price               (for flat_tour / per_day)
     expected_base = MAX(expected_base, tariff_ftl_rate.min_price)

  4. expected_base = result from step 3

  5. expected_diesel = expected_base × (diesel_floater.pct / 100)
     (diesel_floater WHERE valid_from ≤ il.shipment_date, latest applicable)

  6. expected_toll = maut_matrix(weight_band, distance_range)
     OR il.toll_amount if carrier itemises it

  7. expected_surcharges = sum(applicable tariff_surcharges)

  8. expected_total = round(expected_base + expected_diesel + expected_toll + expected_surcharges)

  9. delta = il.line_total - expected_total
     delta_pct = delta / expected_total × 100

  10. Write to shipment_benchmark:
      status = 'unter'    (delta_pct < -threshold)
             | 'im_markt' (-threshold ≤ delta_pct ≤ threshold)
             | 'drüber'   (delta_pct > threshold)
             | 'no_tariff'
```

**Result table:** `shipment_benchmark` (already exists)

**API:** `GET /api/reports/:project_id/invoice-verification`

---

### 5.2 Module 2: Own vs. Carrier Benchmark (Eigenversand-Vergleich)

**Question:** "What does my own delivery cost per stop vs. a carrier?"

This module is **opt-in, client-dependent.** It activates only when tour data is uploaded
for a project. Clients without own fleet simply don't use it.

**Cost model for own delivery:**

```
Per tour:
  fixed_cost = fleet_vehicle.fixed_cost_per_day
  fuel_cost  = own_tour.distance_km × fleet_vehicle.variable_cost_per_km
  driver_cost = own_tour.duration_hours × fleet_driver.hourly_rate
  total_tour_cost = round(fixed_cost + fuel_cost + driver_cost)

Per stop (average):
  own_cost_per_stop = total_tour_cost / own_tour.stop_count

Comparable carrier cost per stop:
  For each stop, find the matching invoice_line via own_tour_stop.shipment_ref
  → carrier_cost_per_stop = sum(matched invoice_lines for this tour) / stop_count

Output per tour:
  own_total_eur, carrier_equivalent_eur, delta_eur, delta_pct
  savings_opportunity = carrier_equivalent_eur - own_total_eur
```

**Aggregations available:**
- By region (dest ZIP prefix)
- By vehicle type / weight class
- By day-of-week, month
- Average across all tours in a project

**Result table:** `own_vs_carrier_benchmark` *(new, belongs to this module)*

**API:** `GET /api/reports/:project_id/own-vs-carrier`

---

### 5.3 Module 3: Outlier & Benchmark Analysis

**Question:** "Where are outliers?"

Three sub-analyses:

**3a. Invoice Delta Outliers**

```sql
-- Flag shipments where delta_pct is beyond 2 standard deviations
-- within the peer group (same carrier × zone × weight_band)
WITH stats AS (
  SELECT carrier_id, zone, weight_band,
         avg(delta_pct) AS mean_delta,
         stddev(delta_pct) AS sd_delta
  FROM shipment_benchmark
  WHERE project_id = ?
  GROUP BY carrier_id, zone, weight_band
)
SELECT sb.*, s.mean_delta, s.sd_delta,
       abs(sb.delta_pct - s.mean_delta) / NULLIF(s.sd_delta, 0) AS z_score
FROM shipment_benchmark sb
JOIN stats s USING (carrier_id, zone, weight_band)
WHERE abs(sb.delta_pct - s.mean_delta) / NULLIF(s.sd_delta, 0) > 2;
```

**3b. Systematic Drift Detection**

```
For each (carrier, zone, weight_band):
  Compute monthly average delta_pct
  If trend over 3+ consecutive months is monotonically increasing → "systematic surcharge drift"
  Flag for review with carrier
```

**3c. Cross-Carrier Rate Benchmark**

```
For lanes that have been served by ≥2 carriers:
  Compare expected_total across carriers for equivalent shipments
  (same zone, same weight band, same date range)
  Output: carrier_id, expected_rate, rank, deviation_from_cheapest_pct
```

**API:** `GET /api/reports/:project_id/outliers`

---

### 5.4 International Lane Architecture

Scope: **EU-27 + UK**. Germany (domestic) is the baseline; international lanes add
complexity in three dimensions: zone logic, currency, and tariff structure.

#### Lane Type Taxonomy

```
domestic_de          DE origin → DE destination       (PLZ zone map, weight matrix)
de_to_at             DE → AT                          }
de_to_ch             DE → CH                          }  Country-level flat rate or
de_to_be / de_to_nl  DE → BE / NL                    }  destination-country zone map
de_to_fr             DE → FR                          }
de_to_pl / de_to_cz  DE → PL / CZ                    }
de_to_gb             DE → UK (post-Brexit customs!)    special: customs clearance fee
eu_domestic_[cc]     Intra-country (e.g. AT → AT)     per-country zone map if available
cross_border         Any other EU-EU pair              country-pair flat rate
```

`lane_type` is stored on `tariff_table`. The zone-lookup and rate-lookup logic branches
on this field.

#### Key Differences vs. Domestic DE

| Aspect | DE domestic | International |
|---|---|---|
| Zone basis | PLZ prefix/exact map | Usually country-wide rate or destination postal zone (varies by carrier) |
| Currency | EUR | EUR common, but GBP for UK; CHF for CH — use `fx_rate` table |
| Customs (UK) | None | MRN / customs clearance fee as separate surcharge line |
| Transit time | 1 day | 2–5 days — affects which diesel_floater date applies |
| Tariff format | Matrix per zone × weight | Often per-country flat rate, sometimes zoned |

#### Tariff Schema Extension for International

The existing `tariff_table` + `tariff_rate` structure handles international if we add:

```sql
-- Add to tariff_table:
ALTER TABLE tariff_table ADD COLUMN dest_country_codes text[];
-- e.g. ['AT'] for DE→AT tariff, ['GB'] for DE→UK, ['DE'] for domestic

-- tariff_rate already has zone column — for international, zone = country code (e.g. 'AT')
-- if the carrier uses a single flat rate per country, zone = country code and weight_from=0

-- For UK: customs surcharge as tariff_surcharge with surcharge_type = 'customs_clearance'
```

#### Currency Handling

International invoices may be in EUR, GBP, or CHF.
- Store `invoice_line.currency` and `invoice_line.line_total` in **original currency**
- Convert to tenant reporting currency (EUR) using `fx_rate` table at invoice date
- All delta calculations in reporting currency; original currency preserved for audit

#### Phase Approach

| Phase | Scope |
|---|---|
| Phase 1 | DE domestic LTL + FTL |
| Phase 2 | DACH (AT, CH) — most common for German Mittelstand |
| Phase 3 | Benelux + FR + PL + CZ |
| Phase 4 | Remaining EU-27 + UK (Brexit customs handling) |

Implement `lane_type` as a text field from day 1 so the schema never changes as phases roll out.

---

### 5.5 Dispute & Resolution Workflow

**Use case:** Consultant identifies an overcharged invoice line → raises a dispute with the
carrier → carrier accepts/rejects → outcome is recorded for audit and reporting.

#### States

```
invoice_line.dispute_status:

  null          No dispute raised (default)
    ↓
  'flagged'     Consultant flagged as potentially incorrect (internal)
    ↓
  'disputed'    Formally raised with carrier (letter/email sent)
    ↓
  'accepted'    Carrier acknowledged overpayment → credit note expected
  'rejected'    Carrier disputes the claim → may escalate or close
  'resolved'    Credit note received or agreed settlement booked
  'closed'      Closed without recovery (written off)
```

#### Schema Addition

```sql
-- Add to invoice_line:
ALTER TABLE invoice_line ADD COLUMN dispute_status text;  -- see states above
ALTER TABLE invoice_line ADD COLUMN dispute_note    text;  -- consultant's reason

-- Dispute history / audit trail
CREATE TABLE invoice_dispute_event (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  invoice_line_id uuid NOT NULL REFERENCES invoice_line(id),
  event_type      text NOT NULL,   -- 'flagged' | 'disputed' | 'accepted' | 'rejected' | 'resolved' | 'closed'
  amount_claimed  numeric(12,2),   -- EUR amount we believe was overcharged
  amount_recovered numeric(12,2),  -- EUR actually recovered (on 'resolved')
  note            text,
  created_by      uuid,            -- user_id
  created_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE invoice_dispute_event ENABLE ROW LEVEL SECURITY;
```

#### Reporting Dimension

Disputes add a **recovery potential** dimension to reports:

```
Total overcharge identified:      12,450 EUR  (shipment_benchmark where status='drüber')
  → Disputed with carriers:        8,200 EUR  (dispute_status = 'disputed' | 'accepted')
  → Already recovered:             3,100 EUR  (dispute_status = 'resolved')
  → Written off:                     950 EUR  (dispute_status = 'closed')
  → Not yet actioned:              4,250 EUR  (status='drüber', dispute_status IS NULL)
```

**API:** `POST /api/invoice-lines/:id/disputes` (raise dispute)
`PATCH /api/invoice-lines/:id/disputes/:event_id` (update status)
`GET  /api/reports/:project_id/dispute-summary`

---

## 6. Modularity & Extensibility

The key architectural principle is: **the analysis layer is decoupled from the ingestion layer.**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CORE (stable)                                     │
│                                                                             │
│   upload → parse → validate → DB (shipment, invoice_*, tariff_*, tour_*)   │
│                                                                             │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ EventEmitter2 events
                                  │ "shipment.imported", "invoice.imported", "tariff.imported"
                                  │
          ┌───────────────────────┼───────────────────────────────────────────┐
          │                       │                                           │
          ▼                       ▼                                           ▼
┌──────────────────┐   ┌──────────────────────┐             ┌──────────────────────────┐
│ InvoiceVerify    │   │ OwnVsCarrier         │             │ OutlierDetection         │
│ Module           │   │ Module               │             │ Module                   │
│ (always active)  │   │ (opt-in, needs fleet)│             │ (always active)          │
└──────────────────┘   └──────────────────────┘             └──────────────────────────┘
          │                       │                                           │
          └───────────────────────┴───────────────────────────────────────────┘
                                  │
                          ┌───────▼────────┐
                          │  Report API    │
                          │  /api/reports  │
                          └────────────────┘
```

**Adding a new analysis module:**

1. Create `src/modules/analysis-<name>/` with its own entities, service, controller
2. Subscribe to core events via `@OnEvent('shipment.imported')`
3. Write results to module-specific tables
4. Expose via `GET /api/reports/:project_id/<name>`
5. No changes to core modules required

**Example future modules:**
- `carbon-footprint` — CO₂ per tonne-km by carrier
- `contract-monitor` — track tariff validity dates, alert before expiry
- `payment-audit` — match invoices against paid amounts from ERP export
- `carrier-performance` — on-time delivery, damage rate (requires delivery confirmation data)
- `seasonal-analysis` — diesel surcharge evolution, rate trends over time

---

## 7. Raw Extraction Storage (Data Lake Pattern)

Every document that enters the system has its raw extraction stored **before** normalization.
This allows re-processing if parsing logic improves, and provides an audit trail.

```sql
CREATE TABLE raw_extraction (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL,
  upload_id    uuid NOT NULL REFERENCES upload(id),
  doc_type     text NOT NULL,   -- 'invoice' | 'tariff' | 'shipment_csv' | 'tour'
  extracted_at timestamptz NOT NULL DEFAULT now(),
  extractor    text NOT NULL,   -- 'template:carrier_xyz_v2' | 'claude-vision' | 'csv-parser'
  confidence   numeric(4,3),
  payload      jsonb NOT NULL,  -- full extracted JSON (as-is from LLM or parser)
  issues       text[],          -- warnings / parse errors
  -- normalization state
  normalized   boolean DEFAULT false,
  normalized_at timestamptz,
  -- retention
  retain_until date             -- computed at insert: extracted_at + tenant.data_retention_years
);
ALTER TABLE raw_extraction ENABLE ROW LEVEL SECURITY;
```

**Why this matters:**
- If Claude Vision produces wrong output today, we can re-extract tomorrow after improving the prompt
- The normalized tables always reflect the *currently best* version of the data
- Corrections from the review UI update `extraction_correction` and trigger re-normalization

### 7.1 Lifecycle & GoBD Compliance

`raw_extraction` stores a JSONB copy of what was parsed from each document. This creates
a data duplication between raw JSON and normalized tables. The lifecycle policy resolves this:

| Data layer | What's stored | Retention | GoBD relevance |
|---|---|---|---|
| `upload` (file buffer / S3) | Original uploaded file (PDF, XLSX, JPG) | **10 years** | GoBD §14b: Belege müssen im Originalformat aufbewahrt werden |
| `raw_extraction.payload` | LLM/parser output JSON | **10 years** | Proves how a document was interpreted — audit trail for disputes |
| Normalized tables (`invoice_header`, `invoice_line`, etc.) | Structured, queryable data | **10 years** | Core business records |
| `extraction_correction` | Human corrections to parsed fields | **10 years** | Evidence of what was changed and why |

**Rule: raw_extraction is never deleted within the retention window.**

```
retain_until = DATE(extracted_at) + tenant.data_retention_years (default: 10)
```

A nightly job sets `retain_until` on insert; a separate archival job flags rows where
`retain_until < CURRENT_DATE` as eligible for deletion (after human sign-off, not automated).

**Storage estimate:** 1,000 invoice lines/month × 2 KB JSON average × 12 months × 10 years
= ~240 MB raw extraction data per client over the full retention period. Negligible in Supabase.

---

## 8. Data Quality Strategy

### 8.1 Validation Rules (enforced at import time)

| Entity | Rule | Action on failure |
|---|---|---|
| `invoice_line` | `sum(line_total)` ≈ `invoice_header.total_net` ± 2% | Hold for review |
| `invoice_line` | `weight_kg` > 0 | Reject line |
| `invoice_line` | `dest_zip` matches `/^\d{5}$/` (DE) | Flag as warning |
| `shipment` | `shipment_reference` not already in DB for this tenant | Deduplicate |
| `tariff_rate` | `weight_from < weight_to` | Reject table |
| `tariff_zone_map` | PLZ prefix within `00000–99999` | Reject entry |

### 8.2 Reconciliation Rate KPI

```
reconciliation_rate = count(invoice_lines with matched shipment)
                    / count(invoice_lines total)
```

Target: ≥ 85%. Below 70% triggers alert. Unmatched lines are stored but excluded from
benchmark calculations; they appear as "unmatched" in reports.

### 8.3 Deduplication

Three layers:
1. **File-level:** SHA256 hash on upload prevents re-processing the same file
2. **Invoice-level:** `UNIQUE(tenant_id, carrier_id, invoice_number)` constraint
3. **Shipment-level:** `UNIQUE(tenant_id, carrier_id, shipment_reference)` constraint

---

## 9. API Surface

```
POST   /api/upload                      Upload any document (auto-detects type)
GET    /api/upload/:id                  Status + parse result
GET    /api/upload/:id/review           Extracted data awaiting confirmation (low confidence)
POST   /api/upload/:id/approve          Confirm and import to core tables
POST   /api/upload/:id/corrections      Submit field corrections

GET    /api/tariffs                     List active tariffs (tenant-scoped)
POST   /api/tariffs/:id/activate        Set as active tariff for a carrier+lane

GET    /api/projects                    List projects
POST   /api/projects                    Create project
GET    /api/projects/:id/summary        Headline KPIs

GET    /api/reports/:project_id/invoice-verification   Module 1
GET    /api/reports/:project_id/own-vs-carrier         Module 2 (if tour data present)
GET    /api/reports/:project_id/outliers               Module 3
GET    /api/reports/:project_id/export?format=xlsx     Full export
```

---

## 10. Open Questions

Clarified items from initial design review are marked ✅. Remaining questions still open.

### Fleet / Own Delivery (Module 2)
- ✅ **Dispatching software:** CSV export assumed as standard input format
- ✅ **Module is client-dependent:** activated only when tour data is present for a project
- How is a "stop" defined per client — by delivery address, customer, or consignment note?
  (Affects cost-per-stop calculation)
- What cost components are available? (€/km, fixed daily vehicle cost, hourly driver rate?)
  A simple input form in the UI could collect this if no system exports it.

### Invoice Matching Quality
- ✅ **Unmatched lines:** always import, classify as `one_time` or `surcharge`, include in
  total cost, exclude from per-shipment tariff benchmarks
- ✅ **Delta threshold:** configurable per tenant via `freight_delta_threshold_pct`
- Should unmatched `one_time` charges be manually taggable by the consultant
  (e.g. "Pauschalpreis Tour X" → mark as expected/unexpected)?

### Tariff Complexity
- ✅ **Sonderkonditionen exist:** `tariff_special_condition` table added; checked before
  standard matrix in benchmark calculation
- ✅ **Service types:** LTL (Stückgut) primary + FTL (Ladung/Charter); `tariff_ftl_rate`
  table added for flat per-km / per-day rates
- ✅ **One-time positions:** always imported, `line_type = 'one_time'`; included in total
  invoice cost, excluded from per-shipment rate benchmark; no concrete examples needed yet —
  the schema is flexible (free-text `description`, no strict categorisation required)
- ✅ **International lanes:** full EU + UK required (not just DACH)
  → See §5.4 — International Lane Architecture
- ✅ **One-time positions:** handled as above

### Workflow & Roles
- ✅ **Operator:** Logistics consultancy uploads data; clients may get read-only access
- ✅ **Dispute workflow:** required — see §5.5 — Dispute & Resolution Workflow
- ✅ **ERP integration:** not required

### Compliance & Retention
- How many years of data need to be retained? (GoBD requires 10 years for invoices in DE)
- Any GDPR constraints on storing delivery addresses (name + address at destination)?

---

## 11. Technology Decision Notes

### Why not Python for the backend?
The existing codebase is NestJS/TypeScript with TypeORM and BullMQ — well-established for
queue-based document processing. The Oxytec evaluator (Python/LangGraph) is a different domain.
Switching would require rewriting 8+ modules. Keep TypeScript.

### Why store raw JSON in PostgreSQL rather than S3 + separate analytics DB?
For the scale expected (Mittelstand: <50k shipments/month), PostgreSQL with JSONB is sufficient
and avoids infrastructure complexity. The `raw_extraction` JSONB column provides the data lake
pattern without a separate store. Revisit if data exceeds 10M rows or queries exceed 10s.

### Why not stream processing (Kafka, etc.)?
Document ingestion is batch-oriented (user uploads a file, waits for parse result).
BullMQ with Redis provides adequate queue semantics. Kafka would be over-engineering.

### LLM for parsing vs. deterministic rules
- **Structured formats** (CSV, known Excel templates): always use deterministic parsers.
  LLM adds latency and non-determinism where it is not needed.
- **Unstructured / scanned documents** (PDF invoices, JPG tariff sheets):
  Claude Vision is appropriate. All LLM output goes through the validation pipeline (§3.2).
- **Carrier/service detection** from ambiguous filenames: Claude text completion, low cost.

---

## 12. Schema Reconciliation (Architecture vs. 001_fresh_schema.sql)

This section documents the delta between what this architecture document specifies and what
is currently in `001_fresh_schema.sql`. All items below require migrations 006+.

### Naming Changes (rename only, no data change)

| Current name in schema | Architecture name | Reason |
|---|---|---|
| `route_trip` | `own_tour` | Communicates business context ("own tour") |
| `route_stop` | `own_tour_stop` | Consistent with parent table rename |

### Missing Tables (create in migrations 006–014)

| Table | Migration | Why needed |
|---|---|---|
| `tariff_surcharge` | 006 | Flexible catch-all for non-standard surcharges beyond `tariff_nebenkosten` |
| `tariff_special_condition` | 007 | Sonderkonditionen + Vereinbarungspreise (flat tour prices) |
| `tariff_ftl_rate` | 008 | FTL/Charter: per-km, per-day, flat-tour rates |
| `tariff_nebenkosten` vs `tariff_surcharge` | — | **Keep both**: `tariff_nebenkosten` is strongly typed for known Nebenkosten; `tariff_surcharge` is the flexible catch-all |
| `raw_extraction` | 009 | Audit trail + re-processing for LLM extractions |
| `invoice_dispute_event` | 010 | Dispute workflow audit trail |
| `extraction_correction` | 011 | Human OCR correction log |
| `fleet_driver` | 012 | Driver hourly rate for own-tour cost model |

### Missing Columns on Existing Tables

| Table | Column | Migration | Why |
|---|---|---|---|
| `tariff_table` | `dest_country_codes text[]` | 009 | International lane routing |
| `invoice_line` | `line_type text` | 006 | `standard` / `vereinbarung` / `surcharge` / `one_time` |
| `invoice_line` | `dispute_status text` | 010 | Dispute workflow state |
| `invoice_line` | `dispute_note text` | 010 | Consultant note for dispute |
| `carrier` | `billing_type_map jsonb` | 006 | Maps carrier billing codes → `line_type` |
| `tenant` | `freight_delta_threshold_pct numeric(5,2)` | 014 | Configurable overpayment threshold |
| `tenant` | `data_retention_years int` | 009 | GoBD retention period (default: 10) |
| `raw_extraction` | `retain_until date` | 009 | Computed retention deadline |

### Design Decision: tariff_nebenkosten + tariff_surcharge (dual approach)

The existing `tariff_nebenkosten` table has strongly-typed columns for every known Nebenkosten
field (Mindestgewichte, Diesel-%, Palettentausch, Avis-Gebühr etc.). This is correct and should
be kept — these fields are queried directly in the benchmark engine.

The new `tariff_surcharge` table is additive: it handles anything that does NOT have a typed
column in `tariff_nebenkosten`. The benchmark engine checks `tariff_nebenkosten` first for
the known fields, then sums any applicable rows from `tariff_surcharge`.

```
Benchmark surcharge calculation:
  diesel    ← tariff_nebenkosten.diesel_floater_pct       (typed, direct)
  maut      ← tariff_nebenkosten.maut_basis + maut_matrix (typed, direct)
  avis      ← tariff_nebenkosten.avis_fee                 (typed, if applicable)
  other     ← SUM(tariff_surcharge WHERE applicable)      (flexible catch-all)
```

### Vereinbarungspreise — Where They Live

A Vereinbarungspreis (e.g. EUR 530 flat for a full tour to PLZ 61118) is stored as:

```sql
INSERT INTO tariff_special_condition (
  tariff_id, tenant_id, condition_type, dest_zip_prefix,
  value, description, valid_from
) VALUES (
  '<tariff_id>', '<tenant_id>', 'flat_tour', '61118',
  530.00, 'Vereinbarungspreis LA 200 Zone 8', '2023-01-01'
);
```

On the invoice line: `line_type = 'vereinbarung'`, `billing_type = '200'`.
The benchmark engine matches `billing_type=200` → `line_type=vereinbarung` via
`carrier.billing_type_map`, then looks up `tariff_special_condition` for the expected price.
