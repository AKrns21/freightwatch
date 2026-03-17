# Schema Design Notes — FreightWatch Supabase
## Version 2.0 / 2026-03-16

---

## Wie die drei Beispieldateien in die Tabellen fließen

### 1. Tarifblatt: `Tarif-Mecu-AT-01_05_2023_Dirk_Beese.pdf`

**Carrier:** Cosi Stahllogistik → Mecu Metallhalbzeug
**Lane:** Österreich (AT), Langgut bis 6m

```
tariff_table:
  name            = 'Stückgutversand Österreich'
  service_type    = 'Langgut bis 6 mtr.'
  lane_type       = 'AT'
  tariff_country  = 'AT'
  carrier_id      = → COSI
  valid_from      = 2023-05-01
  valid_until     = 2023-07-31
  origin_info     = 'ab Hagen'
  delivery_cond.  = 'frei Haus'
  maut_included   = TRUE           ← "inkl. Maut"

tariff_rate (35 Zeilen = 5 Zonen × 7 Gewichtsbänder):
  zone=1, 1–200kg   → 216.50
  zone=1, 201–300kg  → 260.20
  zone=1, 201–500kg  → 324.00
  ...
  zone=6, 1501–2000kg → 681.40

tariff_zone_map (9 Einträge, AT = 1-stellige PLZ-Prefixe):
  country=AT, plz='1', zone=1
  country=AT, plz='2', zone=1
  country=AT, plz='3', zone=3    ← Zone 2 existiert nicht!
  country=AT, plz='4', zone=4
  country=AT, plz='5', zone=4
  country=AT, plz='6', zone=5
  country=AT, plz='7', zone=6
  country=AT, plz='8', zone=6
  country=AT, plz='9', zone=6

tariff_nebenkosten:
  diesel_floater_pct        = NULL  ← "zzgl. Dieselfloater" (Wert separat)
  eu_mobility_surcharge_pct = 5.0   ← "akt. 5 Prozent"
  min_weight_pallet_kg      = 500
  min_weight_cbm_kg         = 300
  min_weight_ldm_kg         = 1650
  min_weight_small_format   = 300
  min_weight_medium_format  = 500
  min_weight_large_format   = 800
  pallet_exchange_note      = 'kein Tausch möglich!'
  transport_insurance       = 'Verzichtskunde'
  oversize_note             = 'ab 2.001 kg - bitte Tagespreise erfragen'
  legal_basis               = 'ADSp 2017'
```

**Besonderheit:** Zone 2 wird übersprungen (Zone 1 = PLZ 1,2; Zone 3 = PLZ 3). Das
ist kein Fehler — manche Carrier nummerieren nicht durchgehend.


### 2. Rechnungen: `as_02-2023_Dirk_Beese.pdf`

**Carrier:** AS Stahl und Logistik
**Empfänger:** Mecu Metallhalbzeug, Kunden-Nr 100066
**Enthält:** 6 Rechnungen (117150, 117179, 117201, 117222, 117242, 117261)
  plus ERP-Deckblätter (Navision KRE-RG+ Belege)

```
invoice_header (6 Stück):
  #1: invoice_number=117261, invoice_date=2023-03-02, total_net=1445.11
      erp_document_number=KRE-RG+096379, erp_barcode=219297
  #2: invoice_number=117242, invoice_date=2023-02-28, total_net=2440.15
      erp_document_number=KRE-RG+096378, erp_barcode=219299
  #3: invoice_number=117222, invoice_date=2023-02-23, total_net=2873.16
  #4: invoice_number=117201, invoice_date=2023-02-16, total_net=2543.46
  #5: invoice_number=117179, invoice_date=2023-02-09, total_net=2078.37
  #6: invoice_number=117150, invoice_date=2023-02-02, total_net=682.35

invoice_line — Beispiele aus Rechnung 117261:

  Zeile 1 (Hauptlauf-Pauschale):
    la_code         = '200'
    billing_type    = 'vereinbarung'
    auftragsnummer  = '230300062'
    tour_number     = '24475'
    weight_kg       = 4428.00         ← Gesamtgewicht Tour
    quantity        = 1.00
    unit            = 'Stück'
    unit_price      = 555.00
    line_total      = 555.00
    dest_address_raw = 'AS Stahl und Logistik GmbH & Co.KG, D-61118 Bad Vilbel'
    dest_zip        = '61118'
    referenz        = 'Hauptlauf 02.03.2023'

  Zeile 2 (Einzelzustellung nach Tarif):
    la_code         = '201'
    billing_type    = 'tarif'
    auftragsnummer  = '230300063'
    tour_number     = '24475'
    weight_kg       = 719.00
    unit            = 'Kg'
    unit_price      = 96.23
    line_total      = 96.23
    dest_address_raw = 'Breidenstein, D-35080 Bad Endbach'
    dest_zip        = '35080'
    referenz        = '446055'
```

**Muster erkannt:**
- Jede Tour hat einen LA 200 "Hauptlauf" (Pauschale, z.B. 530 oder 555 EUR)
- Danach folgen N × LA 201 "Fracht lt. Tarif" (Einzelzustellungen nach Gewicht)
- Tour-Nummer verbindet die Positionen einer Tour
- Ladestelle = immer Mecu Velbert (Origin)
- Entladestelle = Endkunde (Destination) mit PLZ


### 3. Routendokumentation: `Routendokumentation_14_03_2022_-_27_03_2022_Dirk_Beese.csv`

**Fahrzeug:** MAN TGL, ME CU 167 (Mecu-eigener LKW)
**Zeitraum:** 14.03.2022 – 27.03.2022
**511 Einzelfahrten** (Leg-by-Leg aus Telematik)

```
vehicle:
  vehicle_type  = 'MAN TGL'
  plate_number  = 'ME CU 167'

route_trip — Beispiel Tag 14.03.2022 (Tour 1):
  trip_date       = 2022-03-14
  departure_time  = 06:19
  return_time     = 14:56
  base_address    = 'Haberstraße 14, 42551 Velbert'
  stop_count      = ~18 (Stopps zwischen Zentrale→Zentrale)
  total_km        = Summe aller Legs

route_stop — Erste Stopps dieser Tour:
  #1: Velbert → Wuppertal Bornberg, 20.4 km, 22 min
  #2: Wuppertal Bornberg → Wuppertal Hölker Feld, 12.3 km, 17 min
  #3: Wuppertal → Radevormwald, 20.1 km, 41 min
  ...
```

**Aggregationslogik für Trip-Erkennung:**
Eine Tour beginnt wenn `Startort = 'Zentrale'` und endet bei nächstem
`Zielort = 'Zentrale'`. Zwischenstopps bei 'Servicecenter' oder ähnlichen
benannten Orten zählen nicht als Tour-Ende.


---

## Wesentliche Design-Entscheidungen

### tariff_zone_map gehört zu tariff_table, nicht zu carrier
In der alten Schema-Version war die Zone-Map direkt an carrier + tenant gekoppelt.
Problem: Ein Carrier kann verschiedene Zonen-Mappings für verschiedene Kunden haben
(Cosi-AT für Mecu hat andere Zonen als Cosi-DE für einen anderen Kunden).
Lösung: zone_map hat FK zu tariff_table, nicht zu carrier.

### Maut als eigene Tabellen
Maut hat eine andere Achse als Fracht-Tarife (Distanz-Zonen statt PLZ-Zonen).
Deshalb eigene Tabellen (maut_table, maut_rate, maut_zone_map) statt alles
in die tariff_rate zu quetschen.

### invoice_line ist absichtlich denormalisiert
Die Rechnungszeile enthält sowohl Adress-Rohdaten (dest_address_raw) als auch
extrahierte Felder (dest_zip). Das ist gewollt: die Rohdaten dienen als
Audit-Trail und Fallback wenn die Extraktion versagt.

### route_trip + route_stop statt flat table
Die Telematik-CSV liefert Einzelfahrten. Für die Analyse "Kosten pro Zustellung"
brauchen wir aber aggregierte Touren. Die Trip-Tabelle speichert die Aggregate,
die Stop-Tabelle die Einzelfahrten. Die Aggregation passiert beim Import.

### Kein service_catalog / service_alias mehr
Die alte Normalisierung über service_catalog war Overengineering.
service_level ist jetzt ein einfaches VARCHAR(20) auf shipment.
