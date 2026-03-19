"""Seed global parsing templates.

Run from the backend/ directory with the venv active:

    python -m app.scripts.seed_templates

Global templates (tenant_id IS NULL) are shared across all tenants.

Design principles
-----------------
* **Broad detection, not carrier-specific.**  Each template uses a single
  header keyword in `detection` so it scores 80% confidence (0.5 keyword +
  0.3 MIME) for any file whose first CSV row contains that keyword as a
  *substring*.  "datum" matches Datum, Versanddatum, Lieferdatum, …  "date"
  matches date, shipment_date, delivery_date, invoice_date, …

* **Exhaustive field aliases in mappings.**  Every shipment field lists every
  plausible column name variant via ``{"keywords": [...]}`` so the parser
  tries them all in order.  Unrecognised columns are silently skipped;
  completeness scoring flags missing data rather than failing the row.

* **Language-split templates.**  German and English variants are separate so
  each scores 80 % against its target language, not 50 % against a combined
  list.

Templates
---------
  1. Sendungsliste – Deutsch (CSV)
  2. Freight List – English (CSV)
  3. Sendungsliste – Deutsch (XLSX)
  4. Freight List – English (XLSX)
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import structlog

from app.db.session import AsyncSessionLocal
from app.models.database import ParsingTemplate
from app.utils.logger import setup_logging

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Exhaustive keyword lists per field
# Each list covers real-world column name variants seen across German and
# international carriers.  The csv_parser tries them in order and takes the
# first non-empty hit.
# ---------------------------------------------------------------------------

# ── Date ────────────────────────────────────────────────────────────────────
_DATE_DE = {"keywords": [
    "Datum", "Versanddatum", "Lieferdatum", "Sendungsdatum",
    "Rechnungsdatum", "Leistungsdatum", "Auftragsdatum",
    "Abholdatum", "Zustelldatum", "datum",
]}
_DATE_EN = {"keywords": [
    "date", "shipment_date", "delivery_date", "invoice_date",
    "dispatch_date", "pickup_date", "collection_date",
    "service_date", "order_date", "ship_date",
]}

# ── Carrier ─────────────────────────────────────────────────────────────────
_CARRIER_DE = {"keywords": [
    "Spediteur", "Frachtführer", "Carrier", "Transporteur",
    "Beförderer", "Dienstleister", "Logistikpartner", "Spedition",
    "spediteur", "frachtführer", "carrier",
]}
_CARRIER_EN = {"keywords": [
    "carrier", "carrier_name", "carrier_id", "freight_carrier",
    "transport_company", "logistics_provider", "shipper",
    "forwarder", "haulier", "hauler",
]}

# ── Origin ZIP ──────────────────────────────────────────────────────────────
_ORIGIN_ZIP_DE = {"keywords": [
    "VonPLZ", "Von PLZ", "Von_PLZ", "AbsenderPLZ", "AbsenderPlz",
    "Absender PLZ", "Absender_PLZ", "LadePLZ", "Lade PLZ",
    "Abgangs PLZ", "AbgangsPLZ", "HerkunftPLZ", "vonplz",
    "absenderplz", "origin_zip", "from_zip",
]}
_ORIGIN_ZIP_EN = {"keywords": [
    "origin_zip", "from_zip", "from_postal", "sender_zip",
    "origin_postal", "pickup_zip", "collection_zip",
    "shipper_zip", "departure_zip", "source_zip",
    "from_postcode", "origin_postcode",
]}

# ── Origin Country ──────────────────────────────────────────────────────────
_ORIGIN_COUNTRY_DE = {"keywords": [
    "AbsenderLand", "VonLand", "HerkunftsLand", "Abgangsland",
    "absenderland", "vonland", "origin_country",
]}
_ORIGIN_COUNTRY_EN = {"keywords": [
    "origin_country", "from_country", "sender_country",
    "shipper_country", "departure_country",
]}

# ── Dest ZIP ────────────────────────────────────────────────────────────────
_DEST_ZIP_DE = {"keywords": [
    "NachPLZ", "Nach PLZ", "Nach_PLZ", "EmpfängerPLZ", "Empfänger PLZ",
    "Empfaenger PLZ", "EmpfaengerPLZ", "ZielPLZ", "Ziel PLZ",
    "Lieferstelle PLZ", "nachplz", "empfängerplz", "zielplz",
    "dest_zip", "to_zip",
]}
_DEST_ZIP_EN = {"keywords": [
    "dest_zip", "to_zip", "to_postal", "recipient_zip",
    "delivery_zip", "destination_zip", "consignee_zip",
    "to_postcode", "dest_postcode", "delivery_postcode",
]}

# ── Dest Country ─────────────────────────────────────────────────────────────
_DEST_COUNTRY_DE = {"keywords": [
    "EmpfängerLand", "NachLand", "ZielLand", "Lieferland",
    "empfängerland", "nachland", "zielland", "dest_country",
]}
_DEST_COUNTRY_EN = {"keywords": [
    "dest_country", "to_country", "recipient_country",
    "delivery_country", "destination_country", "consignee_country",
]}

# ── Weight ───────────────────────────────────────────────────────────────────
_WEIGHT_DE = {"keywords": [
    "Gewicht", "Gewicht (kg)", "Gewicht kg", "Gewicht_kg",
    "GewichtKG", "Gesamtgewicht", "Ladegewicht", "Frachtgewicht",
    "Abrechnungsgewicht", "RechGewicht", "Rechnungsgewicht",
    "gewicht", "weight_kg", "weight",
]}
_WEIGHT_EN = {"keywords": [
    "weight_kg", "weight", "gross_weight", "chargeable_weight",
    "billed_weight", "actual_weight", "kg", "weight_gross",
    "total_weight", "shipment_weight",
]}

# ── Total Amount ─────────────────────────────────────────────────────────────
_TOTAL_DE = {"keywords": [
    "Betrag", "Gesamtbetrag", "Bruttobetrag", "Rechnungsbetrag",
    "Kosten", "Frachtkosten", "Gesamtkosten", "Transportkosten",
    "Nettobetrag", "Preis", "Gesamtpreis", "Frachtpreis",
    "Abrechnungsbetrag", "Entgelt", "Gesamtentgelt",
    "betrag", "gesamtbetrag", "actual_total_amount",
]}
_TOTAL_EN = {"keywords": [
    "actual_total_amount", "total_amount", "total_cost", "total",
    "freight_cost", "invoice_amount", "gross_amount", "net_amount",
    "line_total", "amount", "charge", "total_charge",
    "billing_amount", "billed_amount", "cost",
]}

# ── Base Amount ───────────────────────────────────────────────────────────────
_BASE_DE = {"keywords": [
    "Grundpreis", "Grundbetrag", "Grundkosten", "Frachtsatz",
    "Basisbetrag", "Nettofracht", "grundpreis", "actual_base_amount",
]}
_BASE_EN = {"keywords": [
    "actual_base_amount", "base_amount", "base_cost", "base_charge",
    "base_rate", "net_freight", "freight_base", "unit_price",
]}

# ── Diesel Surcharge ──────────────────────────────────────────────────────────
_DIESEL_DE = {"keywords": [
    "Dieselzuschlag", "Kraftstoffzuschlag", "Energiezuschlag",
    "Treibstoffzuschlag", "Kraftstoffkosten",
    "dieselzuschlag", "actual_diesel_amount",
]}
_DIESEL_EN = {"keywords": [
    "actual_diesel_amount", "diesel_amount", "diesel_surcharge",
    "fuel_surcharge", "fuel_supplement", "energy_surcharge",
]}

# ── Toll ──────────────────────────────────────────────────────────────────────
_TOLL_DE = {"keywords": [
    "Maut", "Mautkosten", "Mautgebühren", "Straßenmaut",
    "maut", "actual_toll_amount",
]}
_TOLL_EN = {"keywords": [
    "actual_toll_amount", "toll_amount", "toll", "road_toll",
    "motorway_toll",
]}

# ── Currency ──────────────────────────────────────────────────────────────────
_CURRENCY_DE = {"keywords": [
    "Währung", "Waehrung", "Devisen", "Valuta",
    "währung", "currency",
]}
_CURRENCY_EN = {"keywords": [
    "currency", "currency_code", "ccy", "iso_currency",
]}

# ── Reference Number ──────────────────────────────────────────────────────────
_REF_DE = {"keywords": [
    "Referenz", "Referenznummer", "Sendungsnummer", "Auftragsnummer",
    "Auftrag", "Frachtbrief", "Frachtbriefnummer", "AWB",
    "Lieferschein", "Lieferscheinnummer", "Barcode",
    "referenz", "reference_number",
]}
_REF_EN = {"keywords": [
    "reference_number", "reference", "shipment_reference",
    "tracking_number", "consignment_number", "waybill",
    "airwaybill", "awb", "parcel_id", "barcode", "order_number",
]}

# ── Service Level ─────────────────────────────────────────────────────────────
_SERVICE_DE = {"keywords": [
    "Produkt", "Service", "Servicelevel", "Dienstleistung",
    "Transportart", "Versandart", "Zustellart",
    "produkt", "service_level",
]}
_SERVICE_EN = {"keywords": [
    "service_level", "service", "product", "service_type",
    "shipment_type", "delivery_type",
]}

# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

_GLOBAL_TEMPLATES: list[dict[str, Any]] = [

    # ── 1. Sendungsliste – Deutsch (CSV) ────────────────────────────────────
    # Detection: "datum" is a substring of virtually every German date column
    # (Datum, Versanddatum, Lieferdatum …).  Single keyword → 80% confidence
    # with any CSV MIME type match.
    {
        "name": "Sendungsliste – Deutsch (CSV)",
        "description": (
            "Generische Sendungsliste mit deutschen Spaltenbezeichnungen. "
            "Erkennt alle gängigen deutschen Trägerformate automatisch."
        ),
        "file_type": "csv",
        "template_category": "shipment_list",
        "detection": {
            "header_keywords": ["datum"],
            "mime_types": ["text/csv", "text/plain"],
        },
        "mappings": {
            "date":                 _DATE_DE,
            "carrier_name":         _CARRIER_DE,
            "origin_zip":           _ORIGIN_ZIP_DE,
            "origin_country":       _ORIGIN_COUNTRY_DE,
            "dest_zip":             _DEST_ZIP_DE,
            "dest_country":         _DEST_COUNTRY_DE,
            "weight_kg":            _WEIGHT_DE,
            "actual_total_amount":  _TOTAL_DE,
            "actual_base_amount":   _BASE_DE,
            "actual_diesel_amount": _DIESEL_DE,
            "actual_toll_amount":   _TOLL_DE,
            "currency":             _CURRENCY_DE,
            "reference_number":     _REF_DE,
            "service_level":        _SERVICE_DE,
        },
    },

    # ── 2. Freight List – English (CSV) ─────────────────────────────────────
    # Detection: "date" is a substring of virtually every English date column.
    {
        "name": "Freight List – English (CSV)",
        "description": (
            "Generic shipment list with English column names. "
            "Covers international and UK/US carrier export formats."
        ),
        "file_type": "csv",
        "template_category": "shipment_list",
        "detection": {
            "header_keywords": ["date"],
            "mime_types": ["text/csv", "text/plain"],
        },
        "mappings": {
            "date":                 _DATE_EN,
            "carrier_name":         _CARRIER_EN,
            "origin_zip":           _ORIGIN_ZIP_EN,
            "origin_country":       _ORIGIN_COUNTRY_EN,
            "dest_zip":             _DEST_ZIP_EN,
            "dest_country":         _DEST_COUNTRY_EN,
            "weight_kg":            _WEIGHT_EN,
            "actual_total_amount":  _TOTAL_EN,
            "actual_base_amount":   _BASE_EN,
            "actual_diesel_amount": _DIESEL_EN,
            "actual_toll_amount":   _TOLL_EN,
            "currency":             _CURRENCY_EN,
            "reference_number":     _REF_EN,
            "service_level":        _SERVICE_EN,
        },
    },

    # ── 3. Sendungsliste – Deutsch (XLSX) ────────────────────────────────────
    {
        "name": "Sendungsliste – Deutsch (XLSX)",
        "description": (
            "Generische Sendungsliste mit deutschen Spaltenbezeichnungen im "
            "Excel-Format."
        ),
        "file_type": "xlsx",
        "template_category": "shipment_list",
        "detection": {
            "header_keywords": ["datum"],
            "mime_types": ["spreadsheet", "excel"],
        },
        "mappings": {
            "date":                 _DATE_DE,
            "carrier_name":         _CARRIER_DE,
            "origin_zip":           _ORIGIN_ZIP_DE,
            "origin_country":       _ORIGIN_COUNTRY_DE,
            "dest_zip":             _DEST_ZIP_DE,
            "dest_country":         _DEST_COUNTRY_DE,
            "weight_kg":            _WEIGHT_DE,
            "actual_total_amount":  _TOTAL_DE,
            "actual_base_amount":   _BASE_DE,
            "actual_diesel_amount": _DIESEL_DE,
            "actual_toll_amount":   _TOLL_DE,
            "currency":             _CURRENCY_DE,
            "reference_number":     _REF_DE,
            "service_level":        _SERVICE_DE,
        },
    },

    # ── 4. Freight List – English (XLSX) ─────────────────────────────────────
    {
        "name": "Freight List – English (XLSX)",
        "description": (
            "Generic shipment list with English column names in Excel format."
        ),
        "file_type": "xlsx",
        "template_category": "shipment_list",
        "detection": {
            "header_keywords": ["date"],
            "mime_types": ["spreadsheet", "excel"],
        },
        "mappings": {
            "date":                 _DATE_EN,
            "carrier_name":         _CARRIER_EN,
            "origin_zip":           _ORIGIN_ZIP_EN,
            "origin_country":       _ORIGIN_COUNTRY_EN,
            "dest_zip":             _DEST_ZIP_EN,
            "dest_country":         _DEST_COUNTRY_EN,
            "weight_kg":            _WEIGHT_EN,
            "actual_total_amount":  _TOTAL_EN,
            "actual_base_amount":   _BASE_EN,
            "actual_diesel_amount": _DIESEL_EN,
            "actual_toll_amount":   _TOLL_EN,
            "currency":             _CURRENCY_EN,
            "reference_number":     _REF_EN,
            "service_level":        _SERVICE_EN,
        },
    },
]


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------


async def seed() -> None:
    """Replace all global templates with the current definitions.

    Existing templates whose name matches a definition are updated in-place.
    Templates no longer in the list are soft-deleted.
    New templates are inserted.
    """
    from sqlalchemy import select

    setup_logging()
    log = logger.bind(script="seed_templates")

    new_names = {t["name"] for t in _GLOBAL_TEMPLATES}

    async with AsyncSessionLocal() as db:
        existing: dict[str, ParsingTemplate] = {
            r.name: r
            for r in (
                await db.execute(
                    select(ParsingTemplate).where(
                        ParsingTemplate.tenant_id.is_(None),
                        ParsingTemplate.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        }

        created = updated = deleted = 0

        # Upsert
        for tpl in _GLOBAL_TEMPLATES:
            if tpl["name"] in existing:
                row = existing[tpl["name"]]
                row.description = tpl.get("description")
                row.file_type = tpl["file_type"]
                row.template_category = tpl["template_category"]
                row.detection = tpl["detection"]
                row.mappings = tpl["mappings"]
                log.info("template_updated", name=tpl["name"])
                updated += 1
            else:
                row = ParsingTemplate(
                    tenant_id=None,
                    name=tpl["name"],
                    description=tpl.get("description"),
                    file_type=tpl["file_type"],
                    template_category=tpl["template_category"],
                    detection=tpl["detection"],
                    mappings=tpl["mappings"],
                    source="seed",
                    usage_count=0,
                )
                db.add(row)
                await db.flush()
                log.info("template_created", name=tpl["name"], id=str(row.id))
                created += 1

        # Soft-delete stale global templates no longer in definitions
        from datetime import UTC, datetime
        for name, row in existing.items():
            if name not in new_names:
                row.deleted_at = datetime.now(UTC)
                log.info("template_removed", name=name)
                deleted += 1

        await db.commit()

    log.info("seed_complete", created=created, updated=updated, deleted=deleted)
    print(f"\nDone — {created} created, {updated} updated, {deleted} removed.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(seed())
    sys.exit(0)
