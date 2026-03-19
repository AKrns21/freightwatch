# FreightWatch — Architecture & Development Guide

**Version:** 1.0
**Date:** 2026-03-19
**Status:** Living document — update when decisions change

---

## 1. System Purpose

FreightWatch analysiert Spediteursrechnungen (Invoices), Tarifblätter (Rate Cards) und Sendungslisten (Shipment CSVs) für MECU (Dirk Beese) und ähnliche Mandanten. Ziel ist die automatische Erkennung von Überpreisungen durch Spediteure.

**Mandanten (Tenants):**
- MECU, Maisach — Spediteure: AS Stahl, COSI, DPD, Gebr. Weiss, Kebasha

---

## 2. Kernprinzip: Fehlerlose Extraktion

> **"ALLES auf einer Seite muss fehlerlos erkannt werden, sonst haben wir ein großes Problem."**

Das bedeutet konkret:
- **Kein Fallback auf Platzhalter** für kritische Felder (Auftragsnummer, Datum, Gewicht, Betrag)
- **Kein Schreiben in die DB** wenn Pflichtfelder fehlen — lieber Fehler werfen und manuell reviewen
- **Validation vor dem Insert**: Jeder Datensatz wird vor dem Speichern geprüft
- **Parsing Issues** sind kein normaler Betriebszustand sondern ein Signal zum Handeln

### Pflichtfelder pro Dokumenttyp

| Dokumenttyp | Pflichtfelder für DB-Insert |
|-------------|----------------------------|
| **Invoice Header** | invoice_number, invoice_date, carrier_name, total_gross |
| **Invoice Line** | shipment_date, la_code, dest_zip oder origin_zip, weight_kg, line_total |
| **Shipment CSV** | date, origin_zip, dest_zip, weight_kg, actual_total_amount, carrier_name |

Wenn ein Pflichtfeld fehlt → `status = NEEDS_REVIEW`, kein DB-Insert, Fehlermeldung mit Zeilennummer.

---

## 3. Dokumenttypen & Quellen

### 3.1 Spediteursrechnungen (invoice)

Gescannte PDFs oder digitale PDFs. Verarbeitung via **Claude Vision API** (claude-sonnet-4-6).

| Spediteur | Format | Besonderheiten |
|-----------|--------|----------------|
| **AS Stahl** | Gescanntes PDF (Fax) | LA 200 (Vereinbarung) + LA 201 (Tarif), Auftragsnummer 9-stellig, Tour 5-stellig |
| **COSI** | PDF | Eigenes Format |
| **DPD** | PDF | Standardformat |
| **Gebr. Weiss** | PDF | Österreich/Schweiz Anteil |
| **Kebasha** | PDF | Haustarif DE |

### 3.2 Sendungslisten (shipment_list / fleet_log)

CSV/Excel-Exporte aus dem ERP des Kunden. Verarbeitung via Template-Matching oder LLM-Analyse.

### 3.3 Tarifblätter (rate_card)

PDF- oder Excel-Dateien von Spediteuren. Verarbeitung via `tariff-pdf-parser.service.ts`.

---

## 4. Parsing-Pipeline

```
Datei-Upload (PDF/CSV/Excel)
        │
        ▼
[1] DEDUPLICATION
    SHA256-Hash → prüfe auf (tenant_id, file_hash) Duplikat
    → Falls Duplikat: return existing upload record
        │
        ▼
[2] UPLOAD RECORD ANLEGEN
    status = PENDING
    Datei gespeichert unter: uploads/{tenant_id}/{hash}.{ext}
        │
        ▼
[3] BULL-QUEUE JOB: "parse-file"
        │
        ▼
[4] ROUTING nach sourceType
    ├── invoice → InvoiceParserService.parseInvoicePdfMulti()
    ├── rate_card → TariffPdfParserService
    └── shipment_list / fleet_log → TemplateMatcher → CsvParserService
        │
        ▼
[5a] INVOICE PARSING (PDF)
    PdfVisionService:
    ├── Text-Layer vorhanden? → Template-Matching
    └── Kein Text / Scan? → Claude Vision API (alle Seiten als PNG)
            │
            ▼
    parseVisionResponse() → InvoiceParseResult[]
    ├── Validierung (Pflichtfelder)
    ├── Bei fehlendem Pflichtfeld → NEEDS_REVIEW, kein Insert
    └── Bei ok → invoice_header + invoice_line Records anlegen
        │
        ▼
[5b] CSV/EXCEL PARSING (Sendungsliste)
    TemplateMatcher → confidence >= 0.8?
    ├── Ja → CsvParserService.parseWithTemplate()
    ├── Nein → LlmParserService.analyzeFile() → confidence >= 0.7?
    │          ├── Ja → parseWithLlmMappings()
    │          └── Nein → status = NEEDS_MANUAL_REVIEW
    └── Shipment-Entities anlegen (mit completeness_score)
        │
        ▼
[6] BENCHMARKING (nur für Shipments)
    TariffEngineService.calculateExpectedCost()
    → ShipmentBenchmark anlegen (expected vs actual)
        │
        ▼
[7] UPLOAD STATUS UPDATE
    PARSED | PARTIAL_SUCCESS | NEEDS_REVIEW | FAILED
```

---

## 5. Datenbankschema — Aktuelle Tabellen

### Wichtige Abhängigkeiten

```
tenant
  └── carrier (via carrier_alias)
  └── upload
        └── invoice_header
              └── invoice_line ──── shipment (match_status)
        └── shipment
              └── shipment_benchmark
```

### Migrations-Workflow

**Regel:** Jede Änderung am Entity → neue SQL-Migrationsdatei.

```bash
# Dateinamen-Konvention: NNN_beschreibung.sql
backend/src/database/migrations/
  001_fresh_schema.sql          ← Initiales Schema
  002_upload_status_enum.sql
  003_invoice_line_validation_constraint.sql
  004_tariff_table_parsing_metadata.sql
  005_upload_missing_columns.sql  ← raw_text_hash, meta, etc.
```

**Migrations ausführen** (kein TypeORM-CLI, manuell via Management API oder SQL-Editor):
```bash
# Via Supabase Management API (wenn psql nicht verfügbar)
RAW_TOKEN=$(security find-generic-password -s "Supabase CLI" -w)
ACCESS_TOKEN=$(echo "${RAW_TOKEN#go-keyring-base64:}" | base64 -d)
python3 -c "import json; print(json.dumps({'query': open('migration.sql').read()}))" | \
  curl -s -X POST "https://api.supabase.com/v1/projects/jvucxzrsiqzcaojnpazu/database/query" \
    -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" -d @-
```

### Entity ↔ DB-Alignment-Check

Vor jedem Test prüfen ob Entity-Felder in DB vorhanden:
```bash
# Spalten der upload-Tabelle in DB
SELECT column_name FROM information_schema.columns
WHERE table_name = 'upload' ORDER BY ordinal_position;
```

---

## 6. AS Stahl Rechnungsformat (Priorität 1)

### Dokumentstruktur
Eine AS-Rechnung enthält typischerweise:

**Header (1x pro Rechnung):**
- Beleg-Nr (= invoice_number), z.B. "117261"
- Rechnungsdatum
- Kunden-Nr (= customer_number), z.B. "100066"
- Zahlungsbedingung ("Sofort rein netto")
- Gesamtbetrag netto + MwSt + brutto

**Positionen (n x pro Rechnung, LA 200 oder LA 201):**
- Leistungsdatum (= shipment_date)
- Auftragsnummer (9-stellig, z.B. "230300073")
- Tour-Nr (5-stellig, z.B. "24475")
- Referenz/Referenzen (Komma-separiert, z.B. "445942, 446243")
- LA-Code ("200" = Vereinbarung, "201" = Tarif)
- Ladestelle (Ort mit PLZ, z.B. "D-82216 Maisach")
- Entladestelle (Ort mit PLZ, z.B. "D-42551 Velbert")
- Gewicht in kg
- Einzelpreis (EUR)
- Gesamtpreis der Position (EUR)

### Vision-Prompt-Anforderungen
Der Claude-Vision-Prompt muss spezifisch auf AS Stahl ausgerichtet sein:
- PLZ aus "D-XXXXX Ort" Format extrahieren
- Mehrere Referenzen als Array speichern
- LA 200 vs LA 201 korrekt zuordnen
- Kommazahlen (deutsches Format: "1.234,56" → 1234.56) korrekt parsen

**→ Nächster Schritt: AS-Stahl-spezifischen Vision-Prompt entwickeln und validieren**

---

## 7. Qualitätssicherung & Test-Workflow

### Goldstandard-Dokumente

Für jeden Spediteur mind. 1 Referenzdokument mit **händisch verifizierten Erwartungswerten**:

```
docs/historic_data/Mecu/
  as 02-2023 Dirk Beese.pdf        ← AS Stahl Referenzrechnung
  cosi 02-2023 Dirk Beese.pdf      ← COSI Referenzrechnung
  AS 04.2022 Dirk Beese.pdf
  ...
```

### Validierungsprozess (vor jedem Merge)

1. **Upload** des Referenzdokuments via API
2. **Extraktion** prüfen: jede Zeile im PDF = eine invoice_line in DB?
3. **Feldvergleich**: Auftragsnummer, Datum, Gewicht, Betrag vs. Originalrechnung
4. **Abweichungen** = Fehler → Prompt anpassen, nicht DB-Schema

### Akzeptanzkriterien

| Kriterium | Schwellwert |
|-----------|------------|
| Zeilendeckung | 100% (alle Rechnungszeilen erkannt) |
| Pflichtfeld-Genauigkeit | 100% (kein Pflichtfeld darf fehlen oder falsch sein) |
| Betragsgenauigkeit | ±0,01 EUR (Rundung erlaubt) |
| PLZ-Extraktion | 100% |
| LA-Code | 100% |

---

## 8. Entwicklungsreihenfolge (Priorisierung)

### Phase 1 — Infrastruktur (✅ abgeschlossen)
- [x] Supabase DB-Verbindung (freightwatch_app role, Session Pooler)
- [x] Backend auf Port 4000
- [x] Migration 005: fehlende upload-Spalten

### Phase 2 — AS Stahl Extraktion (🔄 aktuell)
- [ ] AS-Stahl-spezifischen Vision-Prompt entwickeln
- [ ] Test mit `as 02-2023 Dirk Beese.pdf` — alle Felder korrekt?
- [ ] Tenant + Carrier Seed-Daten (MECU, AS_STAHL) anlegen
- [ ] Upload-Flow End-to-End ohne Fehler

### Phase 3 — Weitere Spediteure
- [ ] COSI Prompt/Template
- [ ] DPD Prompt/Template
- [ ] Gebr. Weiss Prompt/Template

### Phase 4 — Matching & Benchmarking
- [ ] Invoice Lines ↔ Shipment CSV matchen
- [ ] Tarifmotor für AS Stahl konfigurieren
- [ ] Delta-Berechnung (Soll vs. Ist)

### Phase 5 — Frontend & Reporting
- [ ] Upload-UI
- [ ] Review-Interface für NEEDS_REVIEW Items
- [ ] Auswertungsreport pro Spediteur

---

## 9. Offene Fragen / Bekannte Lücken

| # | Problem | Auswirkung | Lösung |
|---|---------|------------|--------|
| 1 | AS-Vision-Prompt generisch, nicht AS-spezifisch | Fehlende/falsche Felder | AS-spezifischen Prompt entwickeln |
| 2 | Kein MECU-Tenant in DB | Upload schlägt fehl (FK-Verletzung) | Seed-Script für Tenant + Carriers |
| 3 | Redis für Bull erforderlich | Bull-Queue startet nicht ohne Redis | `brew services start redis` oder Docker |
| 4 | Diesel-Basis "total" nicht implementiert | MVP-Limitierung im Tarifmotor | Phase 4 |
| 5 | Shipment-Matching-Algorithmus fehlt | Keine Verknüpfung Rechnung ↔ Sendung | Phase 4 |
| 6 | LLM-Parser hat hardcoded confidence 0.7 | Grenzfälle werden abgelehnt | Konfigurierbar machen |

---

## 10. Lokale Entwicklungsumgebung

```bash
# Voraussetzungen
brew services start redis        # Bull-Queue benötigt Redis

# Backend starten
cd backend
npm install
npm run start:dev                # Port 4000

# Frontend starten (separates Terminal)
cd frontend
npm install
npm run dev                      # Port 5173

# API testen
curl -X POST http://localhost:4000/api/upload \
  -F "file=@docs/historic_data/Mecu/as 02-2023 Dirk Beese.pdf" \
  -F "sourceType=invoice"
```

**Supabase-Verbindung:** Siehe [CLAUDE.md](../CLAUDE.md) → Abschnitt "Supabase Database Connection"
