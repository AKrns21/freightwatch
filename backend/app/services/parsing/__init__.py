"""Tariff parsing services — shared result types.

Both TariffXlsxParser and TariffPdfParser return a TariffParseResult
containing entries, zone maps, and nebenkosten ready for DB import.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class TariffEntry:
    """Single price entry: zone × weight band → price."""

    zone: int
    weight_min: Decimal
    weight_max: Decimal
    base_amount: Decimal
    currency: str
    service_level: str | None = None


@dataclass
class ZoneInfo:
    """Zone definition with optional PLZ description."""

    zone_number: int
    label: str
    plz_description: str | None = None


@dataclass
class PlzZoneMapping:
    """PLZ prefix → zone number mapping."""

    country_code: str
    plz_prefix: str
    match_type: str  # 'prefix' | 'exact'
    zone: int


@dataclass
class NebenkostenInfo:
    """Parsed surcharge / conditions block."""

    diesel_floater_pct: Decimal | None = None
    eu_mobility_surcharge_pct: Decimal | None = None
    maut_included: bool | None = None
    delivery_condition: str | None = None
    min_weight_pallet_kg: Decimal | None = None
    min_weight_per_cbm_kg: Decimal | None = None
    min_weight_per_ldm_kg: Decimal | None = None
    raw_items: dict[str, Any] = field(default_factory=dict)


@dataclass
class TariffParseResult:
    """Complete result of parsing a carrier tariff sheet.

    Ready for import into TariffTable + TariffRate + TariffZoneMap + TariffNebenkosten.
    """

    carrier_name: str
    lane_type: str  # domestic_de | domestic_at | domestic_ch | de_to_ch | de_to_at
    valid_from: date
    valid_until: date | None
    currency: str
    entries: list[TariffEntry]
    zones: list[ZoneInfo]
    zone_maps: list[PlzZoneMapping]
    nebenkosten: NebenkostenInfo | None
    parsing_method: str  # 'xlsx' | 'template' | 'llm'
    confidence: Decimal
    issues: list[str]
    carrier_id: str | None = None  # set by caller after DB carrier lookup
    raw_structure: dict[str, Any] = field(default_factory=dict)
