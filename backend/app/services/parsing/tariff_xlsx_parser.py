"""Tariff XLSX parser — pandas-based extraction of carrier rate sheets.

Detects zone matrix, weight bands, PLZ zone mappings, and Nebenkosten
(diesel, maut, minimums) from carrier tariff spreadsheets.

Typical XLSX layout:
  Sheet 1: Price matrix with zone columns and weight-band rows
           Zone 1 | Zone 2 | Zone 3 | ...
  bis 50 kg  19.10 | 19.62 | 22.22 | ...
  bis 100 kg 25.06 | 28.72 | 32.71 | ...

  Sheet 2 (optional): PLZ → zone mappings
  Sheet N (optional): Nebenkosten section
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from app.services.parsing import (
    NebenkostenInfo,
    PlzZoneMapping,
    TariffEntry,
    TariffParseResult,
    ZoneInfo,
)
from app.utils.error_handler import handle_service_errors
from app.utils.logger import get_logger
from app.utils.round import round_monetary

logger = get_logger(__name__)

# ── keyword patterns for Nebenkosten detection ──────────────────────────────
_DIESEL_RE = re.compile(r"diesel", re.I)
_EU_MOBILITY_RE = re.compile(r"eu[\s\-]?mobilit|european\s+mobility", re.I)
_MAUT_RE = re.compile(r"maut|toll\b", re.I)
_MIN_PALLET_RE = re.compile(r"mindest.{0,20}palet|palet.{0,20}mindest", re.I)
_MIN_CBM_RE = re.compile(r"je\s+cbm|mindest.{0,10}cbm|cbm.{0,10}mindest", re.I)
_MIN_LDM_RE = re.compile(r"je\s+ldm|mindest.{0,10}ldm|ldm.{0,10}mindest", re.I)
_DELIVERY_RE = re.compile(r"frei\s+haus|ab\s+werk|lieferbedingung", re.I)
_CURRENCY_RE = re.compile(r"\b(EUR|CHF|GBP|USD|PLN)\b")
_ZONE_HEADER_RE = re.compile(r"Zone\s+(\w+)", re.I)
_WEIGHT_BIS_RE = re.compile(r"bis\s+([\d.,]+)\s*kg", re.I)
_COMPANY_SUFFIX_RE = re.compile(
    r"([A-ZÄÖÜ][A-Za-zäöüÄÖÜß\s&.,\-]{3,60}"
    r"(?:GmbH|AG|KG|OHG|e\.K\.|GmbH\s*&\s*Co\.\s*KG)[^\n]{0,40})"
)
_DATE_LABEL_RE = {
    "valid_from": re.compile(
        r"(?:Gültig\s+ab|Valid\s+from|Stand|gültig\s+ab|ab\s+dem)[:\s]+"
        r"(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2})",
        re.I,
    ),
    "valid_until": re.compile(
        r"(?:Gültig\s+bis|Valid\s+until|bis\s+zum)[:\s]+"
        r"(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2})",
        re.I,
    ),
}


# ── module-level helpers ─────────────────────────────────────────────────────


def _parse_eu_number(value: str) -> float | None:
    """Parse European number format (comma decimal / dot thousands).

    Examples: "62,20" → 62.2, "1.234,56" → 1234.56, "1.650" → 1650, "52" → 52.

    Heuristic for dot-only: if the dot precedes exactly 3 digits (European
    thousands separator), remove the dot. Otherwise treat as decimal point.
    """
    if not value:
        return None
    cleaned = value.strip()
    dot_pos = cleaned.rfind(".")
    comma_pos = cleaned.rfind(",")
    if dot_pos > -1 and comma_pos > -1:
        if dot_pos > comma_pos:
            normalized = cleaned.replace(",", "")  # US: 1,234.56
        else:
            normalized = cleaned.replace(".", "").replace(",", ".")  # EU: 1.234,56
    elif comma_pos > -1:
        normalized = cleaned.replace(",", ".")
    elif dot_pos > -1:
        # Dot only: check if it's a thousands separator (exactly 3 digits follow)
        after_dot = cleaned[dot_pos + 1:]
        if len(after_dot) == 3 and after_dot.isdigit() and dot_pos > 0:
            normalized = cleaned.replace(".", "")  # EU thousands: 1.650 → 1650
        else:
            normalized = cleaned  # decimal point: 62.20 stays
    else:
        normalized = cleaned
    try:
        return float(normalized)
    except ValueError:
        return None


def _parse_zone_label(label: str) -> int:
    """Convert a zone label (Roman numeral or integer string) to int."""
    trimmed = label.strip().upper()
    try:
        return int(trimmed)
    except ValueError:
        pass
    roman_map = {
        "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
        "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
    }
    return roman_map.get(trimmed, 1)


def _parse_date(value: str) -> date | None:
    """Parse dd.mm.yyyy, dd/mm/yyyy, or yyyy-mm-dd into a date."""
    if not value:
        return None
    trimmed = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", trimmed)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", trimmed)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100:
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def _validate_entries(entries: list[TariffEntry]) -> list[str]:
    """Return a list of validation issues (empty = all OK)."""
    issues: list[str] = []
    for e in entries:
        if e.zone < 0:
            issues.append(f"Invalid zone: {e.zone}")
        if e.weight_min < 0 or e.weight_max < e.weight_min:
            issues.append(f"Invalid weight range: {e.weight_min}–{e.weight_max}")
        if e.base_amount <= 0:
            issues.append(f"Invalid price for zone {e.zone}: {e.base_amount}")
    return issues


def _calculate_confidence(entries: list[TariffEntry], method: str) -> Decimal:
    """Confidence score: baseline × fraction of fully-complete entries."""
    if not entries:
        return Decimal("0")
    baseline = {
        "template": Decimal("0.95"),
        "llm": Decimal("0.85"),
        "xlsx": Decimal("0.90"),
    }.get(method, Decimal("0.85"))
    complete = sum(
        1 for e in entries
        if e.zone >= 0
        and e.weight_min is not None
        and e.weight_max is not None
        and e.base_amount > 0
    )
    coverage = Decimal(complete) / Decimal(len(entries))
    return min(Decimal("1.0"), max(Decimal("0.0"), baseline * coverage))


# ── parser class ─────────────────────────────────────────────────────────────


class TariffXlsxParser:
    """Parse carrier tariff XLSX sheets into structured TariffParseResult."""

    @handle_service_errors("tariff_xlsx_parse")
    async def parse(
        self,
        dataframes: list[pd.DataFrame],
        filename: str,
    ) -> TariffParseResult:
        """Parse XLSX tariff sheet from DataFrames.

        Args:
            dataframes: One DataFrame per sheet (from DocumentService).
            filename:   Original filename (used for metadata hints).

        Returns:
            TariffParseResult with entries, zone maps, and nebenkosten.
        """
        issues: list[str] = []
        if not dataframes:
            raise ValueError("No DataFrames provided for XLSX parsing")

        entries: list[TariffEntry] = []
        zones: list[ZoneInfo] = []
        zone_maps: list[PlzZoneMapping] = []
        nebenkosten: NebenkostenInfo | None = None
        currency = "EUR"
        valid_from: date | None = None
        valid_until: date | None = None
        carrier_name: str | None = None
        lane_type = "domestic_de"

        for df_idx, df in enumerate(dataframes):
            if df.empty:
                continue

            # Detect currency from any cell (first non-EUR wins)
            if currency == "EUR":
                detected = self._detect_currency(df)
                if detected and detected != "EUR":
                    currency = detected

            # Lane type heuristic from content
            detected_lane = self._detect_lane_type(df)
            if detected_lane != "domestic_de":
                lane_type = detected_lane

            # Metadata extraction (carrier name, dates) from first sheet only
            if df_idx == 0:
                meta = self._extract_metadata(df)
                carrier_name = carrier_name or meta.get("carrier_name")
                valid_from = valid_from or meta.get("valid_from")
                valid_until = valid_until or meta.get("valid_until")

            # Zone matrix detection
            matrix_result = self._find_zone_matrix(df)
            if matrix_result is not None:
                header_row_idx, zone_cols = matrix_result
                sheet_entries = self._extract_entries(df, header_row_idx, zone_cols, currency)
                if sheet_entries:
                    entries.extend(sheet_entries)
                    for col_idx, zone_num in zone_cols.items():
                        if not any(z.zone_number == zone_num for z in zones):
                            if header_row_idx < 0:
                                label = str(df.columns[col_idx]).strip()
                            else:
                                label = str(df.iloc[header_row_idx, col_idx]).strip()
                            zones.append(ZoneInfo(zone_number=zone_num, label=label))

            # PLZ zone map extraction
            sheet_maps = self._extract_plz_maps(df)
            if sheet_maps:
                zone_maps.extend(sheet_maps)

            # Nebenkosten extraction (first sheet that has any data wins)
            if nebenkosten is None:
                nebenkosten = self._extract_nebenkosten(df)

        if not entries:
            issues.append("No tariff entries found in XLSX")

        issues.extend(_validate_entries(entries))
        confidence = _calculate_confidence(entries, "xlsx")

        return TariffParseResult(
            carrier_name=carrier_name or "Unknown",
            lane_type=lane_type,
            valid_from=valid_from or date.today().replace(month=1, day=1),
            valid_until=valid_until,
            currency=currency,
            entries=entries,
            zones=sorted(zones, key=lambda z: z.zone_number),
            zone_maps=zone_maps,
            nebenkosten=nebenkosten,
            parsing_method="xlsx",
            confidence=confidence,
            issues=issues,
        )

    # ── zone matrix detection ────────────────────────────────────────────────

    def _find_zone_matrix(
        self, df: pd.DataFrame
    ) -> tuple[int, dict[int, int]] | None:
        """Scan for a zone header row in column names or first 40 data rows.

        When pandas reads an XLSX the first row often becomes the column
        header, so we check df.columns first (represented as row -1).

        Returns (header_row_idx, {col_idx: zone_number}) or None.
        header_row_idx == -1 means the zone labels are the column names.
        """
        # Check column names first (row -1)
        zone_cols: dict[int, int] = {}
        for col_idx, col_name in enumerate(df.columns):
            m = _ZONE_HEADER_RE.search(str(col_name))
            if m:
                zone_cols[col_idx] = _parse_zone_label(m.group(1))
        if len(zone_cols) >= 2:
            logger.info(
                "xlsx_zone_matrix_found",
                row="columns",
                zone_count=len(zone_cols),
            )
            return -1, zone_cols

        # Then scan data rows
        for row_idx in range(min(40, len(df))):
            row = df.iloc[row_idx]
            zone_cols = {}
            for col_idx, val in enumerate(row):
                m = _ZONE_HEADER_RE.search(str(val))
                if m:
                    zone_cols[col_idx] = _parse_zone_label(m.group(1))
            if len(zone_cols) >= 2:
                logger.info(
                    "xlsx_zone_matrix_found",
                    row=row_idx,
                    zone_count=len(zone_cols),
                )
                return row_idx, zone_cols
        return None

    def _extract_entries(
        self,
        df: pd.DataFrame,
        header_row_idx: int,
        zone_cols: dict[int, int],
        currency: str,
    ) -> list[TariffEntry]:
        """Extract price entries from rows below the zone header.

        header_row_idx == -1 means zone labels are the column names,
        so data starts at row 0.
        """
        entries: list[TariffEntry] = []
        prev_weight_to = Decimal(0)
        start_row = 0 if header_row_idx < 0 else header_row_idx + 1

        for row_idx in range(start_row, len(df)):
            row = df.iloc[row_idx]
            weight_max = self._find_weight_max(row)
            if weight_max is None:
                continue

            weight_from = prev_weight_to + Decimal(1) if prev_weight_to > 0 else Decimal(1)
            prev_weight_to = weight_max

            for col_idx, zone_num in zone_cols.items():
                if col_idx >= len(row):
                    continue
                val = row.iloc[col_idx]
                if pd.isna(val):
                    continue
                price = _parse_eu_number(str(val))
                if price is None or price <= 0:
                    continue
                entries.append(
                    TariffEntry(
                        zone=zone_num,
                        weight_min=weight_from,
                        weight_max=weight_max,
                        base_amount=round_monetary(Decimal(str(price))),
                        currency=currency,
                    )
                )
        return entries

    def _find_weight_max(self, row: pd.Series) -> Decimal | None:
        """Look for a 'bis X kg' weight upper bound in a row."""
        for val in row:
            m = _WEIGHT_BIS_RE.search(str(val))
            if m:
                n = _parse_eu_number(m.group(1))
                if n is not None and n > 0:
                    return Decimal(str(n))
        return None

    # ── PLZ zone map extraction ──────────────────────────────────────────────

    def _extract_plz_maps(self, df: pd.DataFrame) -> list[PlzZoneMapping]:
        """Detect PLZ/Zone column headers and extract mappings."""
        plz_col: int | None = None
        zone_col: int | None = None
        country_col: int | None = None
        header_row: int | None = None

        for row_idx in range(min(10, len(df))):
            row = df.iloc[row_idx]
            for col_idx, val in enumerate(row):
                val_lower = str(val).strip().lower()
                if re.search(r"plz|postleitzahl|postal\s*code", val_lower):
                    plz_col = col_idx
                elif re.search(r"^zone$|^zone\b", val_lower) and zone_col is None:
                    zone_col = col_idx
                elif re.search(r"\bland\b|country|cc\b", val_lower) and country_col is None:
                    country_col = col_idx
            if plz_col is not None and zone_col is not None:
                header_row = row_idx
                break

        if plz_col is None or zone_col is None or header_row is None:
            return []

        maps: list[PlzZoneMapping] = []
        for row_idx in range(header_row + 1, len(df)):
            data_row = df.iloc[row_idx]
            plz_val = str(data_row.iloc[plz_col]).strip() if plz_col < len(data_row) else ""
            zone_val = str(data_row.iloc[zone_col]).strip() if zone_col < len(data_row) else ""

            if not plz_val or plz_val.lower() in ("nan", "none", ""):
                continue

            zone_n = _parse_eu_number(zone_val)
            if zone_n is None:
                zone_n = float(_parse_zone_label(zone_val))
            if zone_n <= 0:
                continue

            country = "DE"
            if country_col is not None and country_col < len(data_row):
                c = str(data_row.iloc[country_col]).strip()
                if len(c) == 2 and c.isalpha():
                    country = c.upper()

            maps.append(
                PlzZoneMapping(
                    country_code=country,
                    plz_prefix=plz_val,
                    match_type="prefix",
                    zone=int(zone_n),
                )
            )
        return maps

    # ── Nebenkosten extraction ───────────────────────────────────────────────

    def _extract_nebenkosten(self, df: pd.DataFrame) -> NebenkostenInfo | None:
        """Scan DataFrame for surcharge data (diesel, maut, minimums)."""
        info = NebenkostenInfo()
        found = False

        # Stringify once for performance
        str_df = df.astype(str)

        for row_idx in range(len(str_df)):
            row = str_df.iloc[row_idx]
            for col_idx, val in enumerate(row):
                val = val.strip()
                if not val or val.lower() in ("nan", "none"):
                    continue

                if _DIESEL_RE.search(val) and info.diesel_floater_pct is None:
                    pct = self._find_pct_in_row(row)
                    if pct is not None:
                        info.diesel_floater_pct = pct
                        found = True

                elif _EU_MOBILITY_RE.search(val) and info.eu_mobility_surcharge_pct is None:
                    pct = self._find_pct_in_row(row)
                    if pct is not None:
                        info.eu_mobility_surcharge_pct = pct
                        found = True

                elif _MAUT_RE.search(val) and info.maut_included is None:
                    rest = " ".join(v for v in row if v not in ("nan", val))
                    if re.search(r"enthalten|included|inkl", rest, re.I):
                        info.maut_included = True
                        found = True
                    elif re.search(r"nicht|not\b|excl|zzgl", rest, re.I):
                        info.maut_included = False
                        found = True

                elif _MIN_PALLET_RE.search(val) and info.min_weight_pallet_kg is None:
                    kg = self._find_kg_in_row(row)
                    if kg is not None:
                        info.min_weight_pallet_kg = kg
                        found = True

                elif _MIN_CBM_RE.search(val) and info.min_weight_per_cbm_kg is None:
                    kg = self._find_kg_in_row(row)
                    if kg is not None:
                        info.min_weight_per_cbm_kg = kg
                        found = True

                elif _MIN_LDM_RE.search(val) and info.min_weight_per_ldm_kg is None:
                    kg = self._find_kg_in_row(row)
                    if kg is not None:
                        info.min_weight_per_ldm_kg = kg
                        found = True

                elif _DELIVERY_RE.search(val) and info.delivery_condition is None:
                    info.delivery_condition = val
                    found = True

        return info if found else None

    def _find_pct_in_row(self, row: pd.Series) -> Decimal | None:
        """Extract a percentage value from a row."""
        for val in row:
            m = re.search(r"([\d.,]+)\s*%", str(val))
            if m:
                n = _parse_eu_number(m.group(1))
                if n is not None and 0 < n < 100:
                    return Decimal(str(n))
        return None

    def _find_kg_in_row(self, row: pd.Series) -> Decimal | None:
        """Extract a kg weight value from a row."""
        for val in row:
            m = re.search(r"([\d.,]+)\s*kg", str(val), re.I)
            if m:
                n = _parse_eu_number(m.group(1))
                if n is not None and 0 < n < 100_000:
                    return Decimal(str(n))
        return None

    # ── metadata helpers ─────────────────────────────────────────────────────

    def _detect_currency(self, df: pd.DataFrame) -> str | None:
        for row_idx in range(min(20, len(df))):
            for val in df.iloc[row_idx]:
                m = _CURRENCY_RE.search(str(val))
                if m:
                    return m.group(1).upper()
        return None

    def _detect_lane_type(self, df: pd.DataFrame) -> str:
        sample = " ".join(
            str(v)
            for row_idx in range(min(30, len(df)))
            for v in df.iloc[row_idx]
        ).lower()
        if "österreich" in sample or "austria" in sample:
            return "domestic_at"
        if "schweiz" in sample or "switzerland" in sample:
            return "domestic_ch"
        return "domestic_de"

    def _extract_metadata(self, df: pd.DataFrame) -> dict[str, Any]:
        """Extract carrier name and validity dates from the first sheet."""
        result: dict[str, Any] = {}
        # Flatten first 20 rows to a single text blob
        text = " ".join(
            str(v)
            for row_idx in range(min(20, len(df)))
            for v in df.iloc[row_idx]
            if str(v).strip() not in ("nan", "None", "")
        )
        m = _COMPANY_SUFFIX_RE.search(text)
        if m:
            result["carrier_name"] = m.group(1).strip()
        for key, pattern in _DATE_LABEL_RE.items():
            m2 = pattern.search(text)
            if m2:
                d = _parse_date(m2.group(1))
                if d:
                    result[key] = d
        return result
