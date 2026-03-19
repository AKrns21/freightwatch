"""CsvParserService — parse carrier shipment CSV files.

Port of backend_legacy/src/modules/parsing/csv-parser.service.ts

Key design decisions vs. TypeScript original:
  - pandas with dtype=str preserves leading zeros in ZIP codes (equivalent to
    PapaParse dynamicTyping: false).
  - Returns ParsedShipment dataclasses rather than SQLAlchemy ORM instances so
    that the parser remains DB-session-free and fully unit-testable.
  - round_monetary() replaces the JS round() helper for financial fields.
  - Dates are Python datetime.date (not datetime), matching the SQLAlchemy model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pandas as pd

from app.services.parsing.column_mapper import normalize as normalize_service
from app.utils.logger import get_logger
from app.utils.round import round_monetary

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Field alias dictionaries (column names → canonical field)
# Keys are lowercased; matching is case-insensitive (headers are lowercased
# before lookup).
# ---------------------------------------------------------------------------

_DATE_ALIASES = ["datum", "date", "versanddatum", "shipment_date", "versand_datum"]
_CARRIER_ALIASES = ["carrier", "spediteur", "frachtführer", "carrier_name"]
_ORIGIN_ZIP_ALIASES = ["vonplz", "from_zip", "origin_zip", "von_plz", "absender_plz"]
_DEST_ZIP_ALIASES = ["nachplz", "to_zip", "dest_zip", "nach_plz", "empfänger_plz"]
_WEIGHT_ALIASES = ["gewicht", "weight", "kg", "weight_kg", "gewicht_kg"]
_COST_ALIASES = ["kosten", "cost", "betrag", "total", "total_amount", "gesamtbetrag"]
_CURRENCY_ALIASES = ["währung", "currency", "ccy", "waehrung"]
_REFERENCE_ALIASES = ["referenz", "reference", "reference_number", "sendungsnummer"]
_SERVICE_ALIASES = ["service", "service_level", "produkt", "service_type"]
_BASE_AMOUNT_ALIASES = ["grundpreis", "base_amount", "base_cost", "grundkosten"]
_DIESEL_ALIASES = [
    "dieselzuschlag",
    "diesel_amount",
    "diesel_surcharge",
    "kraftstoffzuschlag",
]
_TOLL_ALIASES = ["maut", "toll_amount", "toll", "mautgebühren"]

_REQUIRED_FIELDS = [
    "date",
    "carrier_name",
    "origin_zip",
    "dest_zip",
    "weight_kg",
    "actual_total_amount",
    "currency",
]
_OPTIONAL_FIELDS = [
    "origin_country",
    "dest_country",
    "service_level",
    "reference_number",
    "actual_base_amount",
    "actual_diesel_amount",
    "actual_toll_amount",
    "length_m",
    "pallets",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ParsedShipment:
    """In-memory representation of one CSV row, ready for ORM persistence."""

    tenant_id: str
    upload_id: str
    extraction_method: str = "csv_direct"
    source_data: dict[str, Any] = field(default_factory=dict)

    date: date | None = None
    carrier_name: str | None = None  # stored in source_data; kept here for convenience
    origin_zip: str | None = None
    origin_country: str = "DE"
    dest_zip: str | None = None
    dest_country: str = "DE"
    weight_kg: Decimal | None = None
    length_m: Decimal | None = None
    pallets: int | None = None
    currency: str | None = None
    actual_total_amount: Decimal | None = None
    actual_base_amount: Decimal | None = None
    actual_diesel_amount: Decimal | None = None
    actual_toll_amount: Decimal | None = None
    reference_number: str | None = None
    service_level: str | None = None
    completeness_score: Decimal | None = None
    missing_fields: list[str] = field(default_factory=list)
    confidence_score: Decimal | None = None


@dataclass
class RowParseError:
    row: int
    error: str
    raw_data: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract(row: dict[str, Any], aliases: list[str]) -> Any:
    """Return the first non-empty value from *row* matching any alias (case-insensitive)."""
    for alias in aliases:
        val = row.get(alias.lower())
        if val is not None and val != "":
            return val
    return None


def _extract_from_template(row: dict[str, Any], mapping: Any) -> Any:
    """Extract a value using a template mapping (string, dict with 'column', or dict with 'keywords')."""
    if mapping is None:
        return None
    if isinstance(mapping, str):
        return row.get(mapping)
    if isinstance(mapping, dict):
        if "column" in mapping:
            return row.get(mapping["column"])
        if "keywords" in mapping and isinstance(mapping["keywords"], list):
            for kw in mapping["keywords"]:
                val = row.get(kw)
                if val is not None and val != "":
                    return val
    return None


def _parse_date(value: Any) -> date | None:
    """Parse a date string using common EU/ISO formats.

    Accepts:
      - dd.mm.yyyy
      - dd/mm/yyyy
      - yyyy-mm-dd
      - Fallback: ISO parsing (no dots to avoid dd.mm.yyyy misinterpretation)
    """
    if value is None:
        return None

    if isinstance(value, date):
        return value

    s = str(value).strip()
    if not s:
        return None

    patterns = [
        (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), "dmy"),
        (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "dmy"),
        (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),
    ]

    for pattern, order in patterns:
        m = pattern.match(s)
        if m:
            a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if order == "ymd":
                year, month, day = a, b, c
            else:
                day, month, year = a, b, c

            if not (1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
                continue
            try:
                parsed = date(year, month, day)
                return parsed
            except ValueError:
                continue

    # ISO fallback — only if no dots (avoids dd.mm.yyyy misinterpretation)
    if "." not in s:
        try:
            parsed_dt = pd.Timestamp(s)
            if 1900 <= parsed_dt.year <= 2100:
                return parsed_dt.date()
        except Exception:
            pass

    logger.warning("unable_to_parse_date", value=s)
    return None


def _parse_number(value: Any) -> float:
    """Parse a numeric string, handling EU and US number formats."""
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        # Remove currency symbols and whitespace
        clean = re.sub(r"[^\d.,-]", "", value)

        dot_pos = clean.rfind(".")
        comma_pos = clean.rfind(",")

        if dot_pos != -1 and comma_pos != -1:
            if dot_pos > comma_pos:
                # US format: 1,234.56
                clean = clean.replace(",", "")
            else:
                # EU format: 1.234,56
                clean = clean.replace(".", "").replace(",", ".")
        elif comma_pos != -1:
            after_comma = clean[comma_pos + 1 :]
            if 0 < len(after_comma) <= 2:
                # Decimal comma: 1234,56
                clean = clean.replace(",", ".")
            else:
                # Thousands comma: 1,234
                clean = clean.replace(",", "")
        elif dot_pos != -1:
            after_dot = clean[dot_pos + 1 :]
            if len(after_dot) > 2:
                # Thousands dot: 1.234
                clean = clean.replace(".", "")
            # else keep as decimal

        try:
            return float(clean)
        except ValueError:
            return 0.0

    return 0.0


def _normalize_weight(value: Any) -> Decimal | None:
    """Parse weight string; return None for negative or non-numeric values."""
    if value is None:
        return None
    s = str(value).replace(",", ".")
    try:
        num = float(s)
    except ValueError:
        return None
    if num < 0:
        return None
    return round_monetary(num)


def _calculate_completeness(
    shipment: ParsedShipment,
) -> tuple[Decimal, list[str]]:
    """Return (score_0_to_100, missing_required_fields)."""
    present_required = 0
    present_optional = 0
    missing: list[str] = []

    for f in _REQUIRED_FIELDS:
        val = getattr(shipment, f, None)
        # carrier_name is stored separately
        if f == "carrier_name":
            val = shipment.carrier_name
        if val is not None and val != "":
            present_required += 1
        else:
            missing.append(f)

    for f in _OPTIONAL_FIELDS:
        val = getattr(shipment, f, None)
        if val is not None and val != "":
            present_optional += 1

    required_score = (present_required / len(_REQUIRED_FIELDS)) * 0.7
    optional_score = (present_optional / len(_OPTIONAL_FIELDS)) * 0.3
    score = round_monetary((required_score + optional_score) * 100)
    return score, missing


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse(
    file_path: str,
    tenant_id: str,
    upload_id: str,
) -> tuple[list[ParsedShipment], float]:
    """Parse a CSV file using built-in alias dictionaries.

    Equivalent to CsvParserService.parse() in the TypeScript implementation.

    Args:
        file_path: Absolute path to the CSV file on disk.
        tenant_id: UUID string for the owning tenant.
        upload_id: UUID string for the associated upload record.

    Returns:
        (shipments, confidence) where confidence ∈ [0, 1].

    Raises:
        OSError: If the file cannot be read.
        pd.errors.ParserError: If the CSV is malformed beyond recovery.
    """
    try:
        df = pd.read_csv(file_path, dtype=str, skip_blank_lines=True)
    except Exception:
        raise

    # Normalize headers: strip + lowercase (mirrors PapaParse transformHeader)
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.dropna(how="all")

    shipments: list[ParsedShipment] = []

    for idx, row_series in df.iterrows():
        row: dict[str, Any] = {k: (None if pd.isna(v) else v) for k, v in row_series.items()}
        try:
            shipment = _map_row(row, tenant_id, upload_id, extraction_method="csv_direct")
            if shipment is not None:
                shipments.append(shipment)
        except Exception as exc:
            logger.error(
                "csv_row_mapping_error",
                file_path=file_path,
                row_index=int(idx) + 1,  # type: ignore[arg-type]
                error=str(exc),
            )

    confidence = (
        float(sum(float(s.completeness_score or 0) for s in shipments) / len(shipments) / 100)
        if shipments
        else 0.0
    )

    logger.info(
        "csv_parse_complete",
        file_path=file_path,
        shipment_count=len(shipments),
        confidence=confidence,
    )
    return shipments, confidence


def parse_with_template(
    file_path: str,
    tenant_id: str,
    upload_id: str,
    mappings: dict[str, Any],
) -> tuple[list[ParsedShipment], list[RowParseError], float]:
    """Parse a CSV file using explicit template column mappings.

    Equivalent to CsvParserService.parseWithTemplate() in TypeScript.

    Args:
        file_path: Absolute path to the CSV file.
        tenant_id: UUID string for the owning tenant.
        upload_id: UUID string for the associated upload record.
        mappings: Template mapping dict (from ParsingTemplate.mappings JSONB).

    Returns:
        (shipments, row_errors, confidence).
    """
    logger.info(
        "csv_parse_with_template_start",
        file_path=file_path,
        upload_id=upload_id,
    )

    try:
        # Headers are NOT lowercased here — template mappings reference exact column names.
        df = pd.read_csv(file_path, dtype=str, skip_blank_lines=True)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all")
    except Exception:
        raise

    shipments: list[ParsedShipment] = []
    row_errors: list[RowParseError] = []

    for idx, row_series in df.iterrows():
        row: dict[str, Any] = {k: (None if pd.isna(v) else v) for k, v in row_series.items()}
        try:
            shipment = _map_row_with_template(row, mappings, tenant_id, upload_id)
            if shipment is not None:
                shipments.append(shipment)
            else:
                row_errors.append(
                    RowParseError(
                        row=int(idx) + 1,  # type: ignore[arg-type]
                        error="Row skipped: missing or invalid date field",
                        raw_data=row,
                    )
                )
        except Exception as exc:
            logger.error(
                "csv_template_row_error",
                upload_id=upload_id,
                row_index=int(idx) + 1,  # type: ignore[arg-type]
                error=str(exc),
            )
            row_errors.append(
                RowParseError(
                    row=int(idx) + 1,  # type: ignore[arg-type]
                    error=str(exc),
                    raw_data=row,
                )
            )

    confidence = (
        float(sum(float(s.completeness_score or 0) for s in shipments) / len(shipments) / 100)
        if shipments
        else 0.0
    )

    logger.info(
        "csv_parse_with_template_complete",
        upload_id=upload_id,
        shipment_count=len(shipments),
        row_error_count=len(row_errors),
        confidence=confidence,
    )
    return shipments, row_errors, confidence


# ---------------------------------------------------------------------------
# Internal row mappers
# ---------------------------------------------------------------------------


def _map_row(
    row: dict[str, Any],
    tenant_id: str,
    upload_id: str,
    extraction_method: str = "csv_direct",
) -> ParsedShipment | None:
    if not row:
        return None

    shipment = ParsedShipment(
        tenant_id=tenant_id,
        upload_id=upload_id,
        extraction_method=extraction_method,
        source_data=dict(row),
    )

    date_val = _extract(row, _DATE_ALIASES)
    parsed_date = _parse_date(date_val)
    if parsed_date is None:
        logger.warning("csv_skipping_row_invalid_date", row=row)
        return None
    shipment.date = parsed_date

    carrier_val = _extract(row, _CARRIER_ALIASES)
    if carrier_val:
        shipment.carrier_name = str(carrier_val)
        shipment.source_data["carrier_name"] = str(carrier_val)

    shipment.origin_zip = _extract(row, _ORIGIN_ZIP_ALIASES)
    shipment.dest_zip = _extract(row, _DEST_ZIP_ALIASES)

    weight_val = _extract(row, _WEIGHT_ALIASES)
    weight = _normalize_weight(weight_val)
    if weight is not None:
        shipment.weight_kg = weight

    cost_val = _extract(row, _COST_ALIASES)
    if cost_val is not None:
        shipment.actual_total_amount = round_monetary(_parse_number(cost_val))

    currency_val = _extract(row, _CURRENCY_ALIASES)
    if currency_val:
        shipment.currency = str(currency_val).upper()[:3]

    ref_val = _extract(row, _REFERENCE_ALIASES)
    if ref_val:
        shipment.reference_number = str(ref_val)[:100]

    service_val = _extract(row, _SERVICE_ALIASES)
    if service_val:
        shipment.service_level = normalize_service(str(service_val))

    base_val = _extract(row, _BASE_AMOUNT_ALIASES)
    if base_val is not None:
        shipment.actual_base_amount = round_monetary(_parse_number(base_val))

    diesel_val = _extract(row, _DIESEL_ALIASES)
    if diesel_val is not None:
        shipment.actual_diesel_amount = round_monetary(_parse_number(diesel_val))

    toll_val = _extract(row, _TOLL_ALIASES)
    if toll_val is not None:
        shipment.actual_toll_amount = round_monetary(_parse_number(toll_val))

    score, missing = _calculate_completeness(shipment)
    shipment.completeness_score = score
    shipment.missing_fields = missing
    shipment.confidence_score = round_monetary(float(score) / 100)

    return shipment


def _map_row_with_template(
    row: dict[str, Any],
    mappings: dict[str, Any],
    tenant_id: str,
    upload_id: str,
) -> ParsedShipment | None:
    if not row:
        return None

    shipment = ParsedShipment(
        tenant_id=tenant_id,
        upload_id=upload_id,
        extraction_method="template",
        source_data=dict(row),
    )

    date_val = _extract_from_template(row, mappings.get("date"))
    parsed_date = _parse_date(date_val)
    if parsed_date is None:
        logger.warning("template_skipping_row_invalid_date", row=row, date_value=date_val)
        return None
    shipment.date = parsed_date

    carrier_val = _extract_from_template(row, mappings.get("carrier_name"))
    if carrier_val:
        shipment.carrier_name = str(carrier_val)
        shipment.source_data["carrier_name"] = str(carrier_val)

    shipment.origin_zip = _extract_from_template(row, mappings.get("origin_zip"))
    shipment.origin_country = (
        _extract_from_template(row, mappings.get("origin_country")) or "DE"
    )
    shipment.dest_zip = _extract_from_template(row, mappings.get("dest_zip"))
    shipment.dest_country = (
        _extract_from_template(row, mappings.get("dest_country")) or "DE"
    )

    weight_val = _extract_from_template(row, mappings.get("weight_kg"))
    if weight_val is not None:
        weight = _normalize_weight(weight_val)
        if weight is not None:
            shipment.weight_kg = weight

    ldm_val = _extract_from_template(row, mappings.get("ldm"))
    if ldm_val is not None:
        shipment.length_m = round_monetary(_parse_number(ldm_val))

    pallets_val = _extract_from_template(row, mappings.get("pallets"))
    if pallets_val is not None:
        try:
            shipment.pallets = int(str(pallets_val).split(".")[0])
        except (ValueError, TypeError):
            pass

    currency_val = _extract_from_template(row, mappings.get("currency"))
    if currency_val:
        shipment.currency = str(currency_val).upper()[:3]

    total_val = _extract_from_template(row, mappings.get("actual_total_amount"))
    if total_val is not None:
        shipment.actual_total_amount = round_monetary(_parse_number(total_val))

    base_val = _extract_from_template(row, mappings.get("actual_base_amount"))
    if base_val is not None:
        shipment.actual_base_amount = round_monetary(_parse_number(base_val))

    diesel_val = _extract_from_template(row, mappings.get("diesel_amount"))
    if diesel_val is not None:
        shipment.actual_diesel_amount = round_monetary(_parse_number(diesel_val))

    toll_val = _extract_from_template(row, mappings.get("toll_amount"))
    if toll_val is not None:
        shipment.actual_toll_amount = round_monetary(_parse_number(toll_val))

    ref_val = _extract_from_template(row, mappings.get("reference_number"))
    if ref_val:
        shipment.reference_number = str(ref_val)[:100]

    service_val = _extract_from_template(row, mappings.get("service_level"))
    if service_val:
        shipment.service_level = normalize_service(str(service_val))

    score, missing = _calculate_completeness(shipment)
    shipment.completeness_score = score
    shipment.missing_fields = missing
    shipment.confidence_score = round_monetary(float(score) / 100)

    return shipment
