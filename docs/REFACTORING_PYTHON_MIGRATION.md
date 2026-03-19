# FreightWatch — Refactoring Plan: Python/FastAPI Migration

**Status:** Entwurf zur internen Prüfung (Review 1 eingearbeitet)
**Datum:** 2026-03-19
**Review:** 2026-03-19
**Entscheidung:** NestJS/TypeScript Backend wird durch Python/FastAPI ersetzt. Supabase-Schema bleibt unverändert. Frontend (React/Vite) bleibt mit Anpassungen (camelCase JSON, Response-Shape-Validierung).

---

## 1. Motivation

Beide Projekte (oxytec_evaluator und freightwatch) erfordern dieselben Kernfähigkeiten:
- Dokumenten-Upload und Extraktion (PDF, XLSX, CSV, gescannte Dokumente via Vision OCR)
- LLM-basiertes Parsing mit strukturiertem JSON-Output
- Deterministische Business-Logic-Services ("Code over Prompt")
- Supabase PostgreSQL mit Multi-Tenant-Isolation (RLS)

Es macht keinen Sinn, diese Infrastruktur in zwei verschiedenen Sprachen zu duplizieren. oxytec_evaluator ist bereits in Python/FastAPI ausgereift — FreightWatch soll denselben Stack nutzen um Synergien zu maximieren.

---

## 2. Was bleibt / Was fliegt raus

### Bleibt unverändert
| Asset | Begründung |
|---|---|
| Supabase PostgreSQL Schema | 15 Migrationen, exzellentes Design mit RLS, Temporal Validity, Surcharges |
| `ARCHITECTURE.md` | Vollständige Produktspezifikation (Section 11 "Why not Python" muss aktualisiert werden — siehe Phase 0) |
| `data/*.json` | Tarif/Rechnung JSON-Fixtures für Tests |
| `CLAUDE.md` | Wird aktualisiert mit neuem Stack |

### Wird archiviert (nicht gelöscht)
| Asset | Aktion | Begründung |
|---|---|---|
| `backend/` (NestJS) | Umbenennen zu `backend_legacy/` | **Referenz-Implementierung** für die gesamte Portierung. Enthält 17.513 Zeilen Business-Logic, 9 Spec-Files mit 51+ Tests, Edge Cases. Erst löschen wenn Python-Backend alle äquivalenten Tests besteht. |

### Wird angepasst
| Asset | Änderung |
|---|---|
| `frontend/` (React/Vite) | API-URL, Response-Shapes, camelCase-Mapping (siehe Phase 6) |
| `docker-compose.yml` | Vereinfacht (kein Redis mehr) |

---

## 3. Neuer Stack

```
FreightWatch (nach Migration)
├── Backend:     FastAPI + SQLAlchemy 2.0 async
├── Datenbank:   Supabase PostgreSQL (unverändert)
├── LLM:         Anthropic Claude (oxytec LLM-Service adaptiert)
├── Vision OCR:  Claude Vision (Single-Model, kein Dual-Model)
├── Queue:       FastAPI BackgroundTasks (kein Redis/Bull)
├── Auth:        JWT Bearer Token (wie bisher)
├── Frontend:    React/Vite + TailwindCSS (minimale Änderungen)
└── Testing:     pytest (Unit / Integration / E2E)
```

---

## 4. Phasenplan

### Phase 0 — Aufräumen & Vorbereitung
**Dauer:** 1 Tag

- `git tag pre-migration` — Snapshot des aktuellen Stands
- `backend/` umbenennen zu `backend_legacy/` (Referenz-Implementierung behalten)
- Neues `backend/` Verzeichnis anlegen (Python/FastAPI Scaffold)
- `docker-compose.yml` durch vereinfachte Version ersetzen (kein Redis, nur Supabase-Proxy falls nötig)
- `CLAUDE.md` aktualisieren mit neuem Stack
- `ARCHITECTURE.md` Section 11 ("Why not Python?") aktualisieren — aktuelle Begründung widerspricht der Migration

---

### Phase 0.5 — API Contract Extraction
**Dauer:** 0,5 Tage

**Zweck:** Das Frontend erwartet exakte Response-Shapes. Bevor das Backend neu geschrieben wird, müssen alle API-Verträge dokumentiert sein.

**Schritte:**
1. Aus `backend_legacy/src/modules/*/` alle Controller und DTOs extrahieren
2. Request/Response-Shapes als JSON-Beispiele dokumentieren (oder OpenAPI Spec generieren)
3. Besonders beachten:
   - **camelCase** in JSON-Keys (NestJS Default) — FastAPI nutzt snake_case
   - Datum-Formate (ISO 8601 vs. lokale Formate)
   - Enum-Werte (String-Encoding)
   - Pagination-Format (offset/limit vs. cursor)
4. Ergebnis: `docs/API_CONTRACT.md` als verbindliche Referenz für Phase 5 und 6

> **Warum kritisch:** Ohne dokumentierten API-Vertrag wird die Frontend-Integration zum Ratespiel.
> FastAPI's `response_model` + `ConfigDict(alias_generator=to_camel)` löst die camelCase-Frage,
> aber nur wenn wir wissen *welche* Fields erwartet werden.

---

### Phase 1 — Fundament
**Dauer:** 2 Tage

**Direkt aus oxytec_evaluator kopieren (verbatim):**
- `app/db/session.py` — Datenbankverbindung, Connection Pool, `get_db` Dependency
- `app/middleware/jwt_auth_middleware.py` — JWT Verifikation
- `app/middleware/request_tracking.py` — Request ID Tracking
- `app/middleware/security_headers.py` — CORS, Security Headers
- `app/utils/logger.py` — structlog JSON Logging
- `app/services/jwt_service.py` — Token Generierung/Verifikation

**Neu schreiben (freightwatch-spezifisch):**

`app/config.py`
```python
class Settings(BaseSettings):
    database_url: str
    db_ssl_required: bool = True
    anthropic_api_key: str
    openai_api_key: str = ""         # Optional, nur für Embedding Fallback
    jwt_secret_key: str
    jwt_expiry_hours: int = 24
    benchmark_tolerance_pct: float = 5.0    # ±5% für unter/im_markt/drüber
    invoice_total_tolerance_pct: float = 2.0 # Summe Zeilen ≈ Header ±2%
    max_upload_size_mb: int = 10
    vision_model: str = "claude-sonnet-4-6"
    upload_processing_concurrency: int = 5
```

`app/middleware/tenant_middleware.py`
```python
# KRITISCH: RLS-Isolation via PostgreSQL Session Variable
# SET LOCAL (nicht SET SESSION!) — reset beim Transaction-Ende
async def get_tenant_db(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    await db.execute(
        text("SET LOCAL app.current_tenant = :tid"),
        {"tid": str(current_user.tenant_id)}
    )
    yield db
```

> **Wichtig:** `SET LOCAL` (nicht `SET SESSION`) ist zwingend. Verhindert Tenant-Context-Leaks
> zwischen Connection-Pool-Wiederverwendungen. Oxytec nutzt `set_config(..., false)` — hier
> verwenden wir `SET LOCAL` direkt im Transaction-Scope.

`app/models/database.py`
SQLAlchemy 2.0 `Mapped[]`-Modelle für alle 20+ Tabellen:
- `Tenant`, `Carrier`, `CarrierAlias`, `Upload`
- `FxRate` (kein RLS — globale Referenzdaten)
- `Project`, `TariffTable`, `TariffRate`, `TariffZoneMap`, `TariffNebenkosten`
- `MautTable`, `MautRate`, `CitySurcharge`, `DieselFloater`
- `InvoiceHeader`, `InvoiceLine`
- `Shipment`, `ShipmentBenchmark`
- `Vehicle`, `FleetCostProfile`, `RouteTrip`, `RouteStop`
- `ParsingTemplate`, `ManualMapping`, `ConsultantNote`, `Report`

> **Bootstrapping-Tipp:** `sqlacodegen` gegen die Live-Datenbank laufen lassen, um initiale
> SQLAlchemy-Modelle zu generieren. Dann manuell bereinigen (Mapped[]-Syntax, Relationships,
> snake_case). Verhindert Typ-Mismatches zwischen TypeORM-Schema und SQLAlchemy-Modellen
> (nullable-Flags, Default-Werte, Constraint-Namen).

`app/utils/round.py`
```python
# KRITISCH für Geldbeträge — Python's round() nutzt Banker's Rounding!
from decimal import Decimal, ROUND_HALF_UP

def round_monetary(value: float, decimals: int = 2) -> float:
    return float(
        Decimal(str(value)).quantize(
            Decimal(10) ** -decimals,
            rounding=ROUND_HALF_UP
        )
    )
```
> Python's eingebautes `round()` rundet halb-zu-gerade (Banker's Rounding).
> Bei Finanzdaten führt das zu Cent-Abweichungen. `ROUND_HALF_UP` spiegelt
> das Verhalten von `round.ts` aus dem NestJS-Backend exakt wider.

---

### Phase 2 — Document Service
**Dauer:** 1–2 Tage

**Adaptiert aus oxytec_evaluator:**

`app/services/document_service.py`
- Einheitlicher Entry Point für alle Formate: PDF, XLSX, CSV, PNG/JPG
- SHA-256 Hash-Berechnung für Deduplizierung (spiegelt `hash.ts`)
- Rückgabe: `DocumentExtractionResult` (strukturiert, nicht nur raw text)

```python
@dataclass
class DocumentExtractionResult:
    file_hash: str
    mime_type: str
    mode: Literal["text", "vision", "xlsx", "csv"]
    page_count: int
    text: str | None          # Text-basierte PDFs
    pages: list[PageImage]    # Gescannte PDFs (base64 für Vision)
    dataframes: list[pd.DataFrame]  # XLSX/CSV
    raw_bytes: bytes
```

`app/services/vision_service.py` — **Single-Model (nur Claude)**
> Oxytec nutzt Dual-Model (Gemini + GPT-5.2) für Qualitätsvergleich.
> Bei Frachtdokumenten ist Claude Vision allein ausreichend — halbiert API-Kosten.

`app/services/document_type_detector.py`
```python
class DocumentTypeDetector:
    def detect(self, filename: str, result: DocumentExtractionResult) -> SourceType:
        # Schritt 1: Dateiname-Heuristik (tarif, rate_card, rechnung, invoice, ...)
        # Schritt 2: Spalten-Pattern für CSV (dest_zip, weight_kg, ...)
        # Schritt 3: LLM-Fallback (Claude, ~1s, nur wenn 1+2 scheitern)
```

---

### Phase 3 — Parsing Pipeline
**Dauer:** 5–7 Tage

> **Anmerkung:** Die 6-stufige Vision Pipeline (Phase 3b) ist der komplexeste Einzelteil
> der Migration. Jede Stage hat eigene Logik, Typen und Fehlerbehandlung. Allein die
> Vision Pipeline benötigt ~3 Tage; die restlichen Parser (3a, 3c, 3d) zusammen ~2-3 Tage.

#### 3a. Tarifblatt Parser

`app/services/parsing/tariff_xlsx_parser.py`
- pandas-basiert
- Erkennt Zonenmatrix, Gewichtsbänder, Grundpreise
- Parst Nebenkosten-Tabellen (Diesel, Maut, Pauschalzuschläge)
- Validiert gegen `ExtractionValidatorService` (Phase 3d)

`app/services/parsing/tariff_pdf_parser.py`
- Portierung von `tariff-pdf-parser.service.ts`
- LLM (Claude) extrahiert strukturiertes JSON
- Schema: `TariffTable + TariffRate[] + TariffZoneMap[]`
- Few-Shot Beispiele aus `/data/*.json` im Prompt

#### 3b. Rechnungs-Parser + 6-stufige Vision Pipeline

Die NestJS Vision Pipeline (6 Stages) wird Stage-für-Stage nach Python portiert:

```
app/services/parsing/vision_pipeline/
├── pipeline_types.py          # Dataclasses (Portierung von pipeline.types.ts)
├── pre_processor.py           # Stage 1: Bildnormalisierung (Pillow)
├── page_classifier.py         # Stage 2: LLM Seitentyp-Erkennung
├── structured_extractor.py    # Stage 3: LLM strukturierte Extraktion pro Seite
├── cross_document_validator.py # Stage 4: Seitenübergreifende Konsistenzprüfung
├── confidence_scorer.py       # Stage 5: Deterministisches Confidence-Scoring
├── review_gate.py             # Stage 6: auto_import / hold_for_review / reject
└── vision_pipeline.py         # Orchestrierung aller 6 Stages
```

> Die TypeScript-Typen in `pipeline.types.ts` werden 1:1 als Python `@dataclass`
> übernommen: `AnnotatedField`, `ExtractedHeader`, `ExtractedLine`, `PageExtractionResult`,
> `ConfidenceScore`, `ReviewAction`, `PipelineResult`

#### 3c. CSV Parser

`app/services/parsing/csv_parser.py`
- pandas mit `dtype=str` (Führende Nullen in PLZ erhalten — wie `dynamicTyping: false`)
- Flexibles Column-Mapping via Alias-Dictionary

`app/services/parsing/column_mapper.py`
- Portierung von `service-mapper.service.ts`
- Gleiche Alias-Logik: `datum`→`shipment_date`, `gewicht`→`weight_kg`, etc.

#### 3d. Extraction Validator Service

`app/services/extraction_validator_service.py`
**Direkter Python-Port** — 100% deterministisch, kein LLM:

| Regel | Entität | Aktion |
|---|---|---|
| `sum(line_total) ≈ header.total_net ±2%` | `invoice_line` | `hold_for_review` |
| `weight_kg > 0` | `invoice_line` | `reject` |
| `dest_zip` matches `^\d{5}$` | `invoice_line` | `warn` |
| `shipment_reference` nicht doppelt | `shipment` | `reject` |
| `weight_from_kg < weight_to_kg` | `tariff_rate` | `reject` |
| PLZ-Prefix `00000–99999` | `tariff_zone_map` | `reject` |

> Die bestehenden Jest-Tests (`extraction-validator.service.spec.ts`, 31 Tests)
> werden direkt als pytest-Tests übernommen.

---

### Phase 4 — Business Logic Services
**Dauer:** 4–5 Tage

Alle Services: deterministisch, kein LLM, 100% testbar ("Code over Prompt").

#### 4a. Zone Calculator
`app/services/zone_calculator_service.py`
- Portierung von `zone-calculator.service.ts`
- SQL: `WHERE plz_prefix ... ORDER BY LENGTH(plz_prefix) DESC` für spezifischsten Match

#### 4b. FX Service
`app/services/fx_service.py`
- Portierung von `fx.service.ts`
- Direkte Rate → Inverse Rate Fallback
- `@lru_cache` für Same-Day Lookups

#### 4c. Tariff Engine Service
`app/services/tariff_engine_service.py`
- **Komplexeste Klasse** (~794 Zeilen in TypeScript, Methoden-für-Methoden-Portierung)

| Methode | Beschreibung |
|---|---|
| `calculate_expected_cost()` | Haupteinstiegspunkt → `BenchmarkResult` |
| `_determine_lane_type()` | Pure Function |
| `_calculate_zone()` | Delegiert an `ZoneCalculatorService` |
| `_find_applicable_tariff()` | SQLAlchemy async Query |
| `_calculate_chargeable_weight()` | Liest `carrier.conversion_rules` JSONB |
| `_find_tariff_rate()` | SQLAlchemy async Query |
| `_calculate_base_amount()` | `rate_per_shipment` ODER `rate_per_kg × weight` |
| `_convert_currency()` | Delegiert an `FxService` |
| `_get_diesel_floater()` | Query mit Datumsbereich, Fallback 18,5% |
| `_estimate_toll()` | Pure Function, Lookup-Tabelle |
| `_create_shipment_benchmark()` | INSERT in `shipment_benchmark` |

> **Kritisch:** `round_monetary()` aus Phase 1 bei **jeder** arithmetischen Operation.

#### 4d. Benchmark, Report & Carrier Management
`app/services/benchmark_service.py`
- Async Bulk-Processing mit `asyncio.Semaphore(5)` (5 gleichzeitige Berechnungen)
- Portierung der Business-Logik aus `report-aggregation.service.ts`

`app/services/report_aggregation_service.py`
- Aggregiert `shipment_benchmark` Rows pro Carrier
- Berechnet `overpay_rate`, `total_savings_potential`, etc.
- Portierung von `report.service.ts` (842 Zeilen) und `report-aggregation.service.ts`

`app/services/carrier_service.py`
- Carrier-Alias-Verwaltung (Portierung von `carrier-alias.entity.ts`)
- Billing-Type-Map Verwaltung (`carrier.billing_type_map` JSONB)

`app/services/template_service.py`
- Parsing-Template-Verwaltung (Portierung von `template.service.ts` + `template-matcher.service.ts`)
- Manual-Mapping-Verwaltung für Human-Review von unklaren Carrier/Service-Zuordnungen

#### 4e. Upload Processor (ersetzt Bull-Queue)
`app/services/upload_processor_service.py`

```python
# Kein Redis/Bull mehr — FastAPI BackgroundTasks reicht für MVP
@router.post("/uploads")
async def create_upload(
    background_tasks: BackgroundTasks, ...
):
    upload = await create_upload_record(...)
    background_tasks.add_task(process_upload, upload.id, tenant_id)
    return {"upload_id": upload.id, "status": "pending"}
```

Pipeline im Background Task:
```
detect_doc_type → parse → validate → store → benchmark (wenn Sendungsdaten)
```

> **Wichtig: Fehlererkennung ohne Bull-Queue**
> FastAPI BackgroundTasks bietet kein Retry, kein Failure-Monitoring, keine Concurrency-Kontrolle.
> Um hängengebliebene Jobs zu erkennen:
> - `upload.updated_at` bei jedem Status-Wechsel aktualisieren
> - Periodischer Health-Check (z.B. via `asyncio.create_task` beim Startup):
>   Uploads mit `status='parsing'` und `updated_at < now() - 5min` → auf `failed` setzen
> - Logging: Jeder Status-Wechsel wird geloggt → Fehler sind im Log sichtbar
> - Bei Bedarf später: Postgres-basierte Job-Queue (`upload` Tabelle als Job-Table) statt Redis

---

### Phase 5 — API Endpoints
**Dauer:** 2–3 Tage

```
app/api/routes/
├── auth.py          # POST /auth/login
├── projects.py      # CRUD /projects
├── uploads.py       # POST /uploads  ← Herzstück
├── shipments.py     # GET /projects/{id}/shipments
├── invoices.py      # GET /projects/{id}/invoices + Zeilen + Disputes
├── tariffs.py       # CRUD /tariff-tables, /zone-maps, /surcharges
├── benchmarks.py    # POST /projects/{id}/benchmarks/calculate
├── reports.py       # POST /projects/{id}/reports + Export
├── carriers.py      # GET /carriers + Alias-Management
└── health.py        # GET /health (Readiness + Liveness)
```

Adaptiert aus oxytec: `app/api/routes/auth.py`, `app/api/deps.py`

**camelCase JSON-Output:**
```python
# In app/models/schemas.py — Basis-Schema für alle Response-Modelle
from pydantic.alias_generators import to_camel

class CamelModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )
```
> Alle Response-Schemas erben von `CamelModel` → automatische camelCase-Konvertierung.
> Damit bleibt der Frontend-TypeScript-Code (`types/index.ts`) unverändert.

**Vertrag:** Alle Endpoints müssen gegen `docs/API_CONTRACT.md` (Phase 0.5) validiert werden.

---

### Phase 6 — Frontend-Integration
**Dauer:** 1–2 Tage

**Änderungen:**
1. `VITE_API_URL` von `http://localhost:4000` auf `http://localhost:8000`
2. Auth-Header-Format prüfen (JWT Bearer bleibt gleich)
3. **Response-Shape-Validierung:** Jeden API-Call gegen `docs/API_CONTRACT.md` prüfen
4. **TypeScript-Interfaces** (`types/index.ts`) anpassen falls nötig:
   - Datum-Formate (Python `datetime.isoformat()` vs. NestJS Date-Serialisierung)
   - Nullable-Felder (Python `None` → JSON `null`)
   - Enum-String-Werte (Python Enum `.value` vs. NestJS String-Unions)
5. **Smoke-Tests:** Jede Seite manuell durchklicken (Projects, Upload, Review, Report)

> **Warum mehr als 0,5 Tage:** Das Frontend hat 1.367 Zeilen TypeScript mit fest verdrahteten
> Interfaces. Selbst mit camelCase-Alias im Backend gibt es erfahrungsgemäß Abweichungen bei
> Datum-Formaten, Pagination, und Error-Response-Shapes.

> Falls später ein vollständiger Rebuild auf Next.js 14 gewünscht wird: nach
> Stabilisierung des Python Backends. Nicht im aktuellen Scope.

---

## 5. Architekturentscheidungen

### 5.1 Kein Redis / Kein Bull
FastAPI `BackgroundTasks` reicht für MVP-Volumen (1.000–4.000 Sendungen/Monat).

**Tradeoffs gegenüber Bull-Queue:**
- Kein automatisches Retry bei Fehlern → Mitigation: `upload.status` Timeout-Detection (siehe Phase 4e)
- Kein Failure-Dashboard → Mitigation: structlog-basiertes Monitoring, `upload.error_detail` Feld
- Keine Concurrency-Kontrolle → Mitigation: `asyncio.Semaphore(5)` im Upload-Processor

**Upgrade-Pfad:** Bei Bedarf Postgres-basierte Job-Queue (`upload` Tabelle als Job-Table, `SELECT ... FOR UPDATE SKIP LOCKED`). Kein Redis-Betrieb nötig.

### 5.2 RLS via `SET LOCAL` (nicht `SET SESSION`)
`SET LOCAL` gilt nur für die aktuelle Transaktion → automatisches Reset am Transaction-Ende.
Verhindert Tenant-Context-Leaks zwischen Connection-Pool-Verbindungen.

### 5.3 Single-Model Vision (nur Claude)
Oxytec nutzt Dual-Model (Gemini + GPT-5.2) wegen hoher Qualitätsvarianz bei beliebigen PDFs.
Frachtdokumente sind stärker strukturiert — Claude Vision allein ist ausreichend.
**Vorteil:** 50% weniger LLM-API-Kosten, keine Google-SDK-Abhängigkeit.

### 5.4 "Code over Prompt" konsequent
- Alle Tarif-Kalkulationen, Zone-Lookups, FX-Umrechnungen, Benchmark-Klassifikationen: **reines Python**
- LLM nur für: Dokumenttyp-Erkennung (Fallback), Tarifblatt-Extraktion aus PDF, Invoice-Vision-Extraktion

### 5.5 Keine Alembic-Migrationen
Schema wird direkt über Supabase SQL Editor verwaltet (wie bisher).
`init_db()` in Produktion nur `SELECT 1` zur Verbindungsprüfung.

---

## 6. Direkte Code-Übernahmen aus oxytec_evaluator

| oxytec Datei | FreightWatch Ziel | Änderungen |
|---|---|---|
| `app/db/session.py` | `app/db/session.py` | `pool_size=10` |
| `app/middleware/jwt_auth_middleware.py` | gleich | `PUBLIC_PATH_PREFIXES` anpassen |
| `app/middleware/request_tracking.py` | gleich | verbatim |
| `app/middleware/security_headers.py` | gleich | verbatim |
| `app/utils/logger.py` | gleich | verbatim |
| `app/services/jwt_service.py` | gleich | verbatim |
| `app/services/auth_service.py` | gleich | kleine Anpassung |
| `app/services/document_service.py` | gleich | stark adaptiert |

---

## 7. Teststrategie

Oxytec's 3-Tier-Strategie spiegeln:

**Unit Tests** (`tests/unit/services/`):
- Alle 31 Jest-Tests aus `extraction-validator.service.spec.ts` → pytest
- `fx.service.spec.ts` → `test_fx_service.py`
- `zone-calculator.service.spec.ts` → `test_zone_calculator_service.py`
- `tariff-engine.service.spec.ts` → `test_tariff_engine_service.py`
- `csv-parser.service.spec.ts` → `test_csv_parser_service.py`
- Fixtures: `/data/*.json` als pytest Fixtures

**Integration Tests** (`tests/integration/`):
- Voller Upload-Pfad: Datei → Parse → Validate → Store → Benchmark
- RLS-Isolation: zwei Tenants, Cross-Contamination unmöglich verifizieren
- **RLS-Test-Setup:** `conftest.py` mit zwei Fixtures (`tenant_a_db`, `tenant_b_db`),
  jeweils mit eigenem `SET LOCAL app.current_tenant`. Test: Tenant A sieht keine Daten von Tenant B.
- **Supabase-spezifisch:** Tests gegen Remote-Supabase (nicht lokale DB) benötigen
  dedizierte Test-Tenants mit eigenen RLS-Policies. Alternativ: lokaler PostgreSQL-Container
  mit `001_fresh_schema.sql` für Tests.

**Schlüsselmetrik:** >50% Überzahlungserkennungsrate auf MECU-Fixture-Daten.

---

## 8. Ablaufsequenz

```
Phase 0   (Aufräumen)              1d      ← git tag, backend_legacy/, ARCHITECTURE.md
Phase 0.5 (API Contract)          0,5d    ← Response-Shapes dokumentieren
    ↓
Phase 1   (Fundament)              2d      ← Voraussetzung für alles
    ↓
Phase 2   (Document Service)       1-2d    ← Voraussetzung für Phase 3
    ↓
Phase 3a  (CSV + XLSX Parser)      }
Phase 3b  (Invoice Vision 6-Stage) } 5-7d  ← Vision Pipeline ist größter Einzelblock (~3d)
Phase 3c  (CSV Parser)             }
Phase 3d  (Extraction Validator)   }       ← 3a/3c/3d parallel entwickelbar
    ↓
Phase 4a/4b (Zone + FX)            }
Phase 4c    (Tariff Engine)        } 4-5d  ← Tariff Engine (~794 Zeilen) ist zweitgrößter Block
Phase 4d    (Benchmark + Report)   }       ← Inkl. Report-Generierung + Carrier/Template-Mgmt
Phase 4e    (Upload Processor)     }       ← Benötigt 3a-3d, 4c
    ↓
Phase 5   (API Endpoints)          2-3d    ← Inkl. camelCase-Mapping, API-Contract-Validierung
    ↓
Phase 6   (Frontend-Integration)   1-2d    ← Response-Shape-Validierung, Smoke-Tests
```

**Gesamtaufwand: 17–23 Tage**

> **Zur Einordnung:** Die NestJS-Codebasis umfasst 17.513 Zeilen TypeScript in 98+ Dateien.
> Die optimistische Schätzung (17d) setzt voraus, dass Claude Code den Großteil der
> Methoden-für-Methoden-Portierung übernimmt. Die pessimistische Schätzung (23d) rechnet
> mit Edge Cases, Debugging und Test-Anpassungen.

---

## 9. Kritische Referenzdateien

| Datei | Warum kritisch |
|---|---|
| `backend_legacy/src/modules/tariff/tariff-engine.service.ts` | Kern-Business-Logic, ~794 Zeilen, Methode für Methode portieren |
| `backend_legacy/src/database/migrations/001_fresh_schema.sql` | Kanonische Schema-Definition — alle SQLAlchemy-Modelle müssen exakt passen |
| `oxytec_evaluator/backend/app/db/session.py` | Pattern für DB-Verbindung, Tenant-Context, `get_db` Dependency |
| `backend_legacy/src/modules/invoice/vision-pipeline/pipeline.types.ts` | Typ-Definitionen für 6-stufige Vision Pipeline → Python Dataclasses |
| `backend_legacy/src/modules/upload/extraction-validator.service.ts` | Alle 6 Validierungsregeln mit exakten Toleranzkonstanten |
| `backend_legacy/src/modules/report/report.service.ts` | Report-Generierung (~842 Zeilen), in Phase 4d portieren |
| `backend_legacy/src/modules/parsing/template.service.ts` | Template-Matching für Carrier-spezifisches Parsing |
| `backend_legacy/src/modules/upload/document-classifier.service.ts` | Dokumenttyp-Erkennung (Heuristik + LLM-Fallback) |

---

## 10. Checkliste: Bereit für Phase 0?

- [ ] `ARCHITECTURE.md` Section 11 Update vorbereitet
- [ ] `git tag pre-migration` gesetzt
- [ ] Zugang zu Supabase-Datenbank für `sqlacodegen` verifiziert
- [ ] Alle 9 Spec-Files (51+ Tests) in `backend_legacy/` identifiziert und als Port-Checkliste dokumentiert
- [ ] `docs/API_CONTRACT.md` Template vorbereitet
- [ ] oxytec_evaluator Infrastruktur-Files (`session.py`, `logger.py`, Middleware) gesichtet

---

*Erstellt: 2026-03-19 — Review 1 eingearbeitet: 2026-03-19*
