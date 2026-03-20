"""DestatisDieselService — fetch monthly Heizöl/Diesel reference prices from GENESIS.

The Statistisches Bundesamt (Destatis) publishes monthly mineral oil prices
via their GENESIS-Online REST API. German freight carriers typically reference
the series for "Heizöl leicht, bei Lieferung von 50–70 hl an Großverbraucher,
frei Verbrauchsstelle, Deutschland" to determine the monthly diesel surcharge.

Lag rule (standard in German freight contracts):
    Surcharge for month M = price published for month M-2
    e.g. January surcharge is based on the November price published in December.

Issue: #63

Key features:
- fetch_month(): get price for a given year/month, hit cache first
- resolve_for_date(): apply the 2-month lag and return the reference price
- refresh_recent(): fetch last 24 months into cache (called on startup / cron)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import DestatisDieselPrice

logger = structlog.get_logger(__name__)

# GENESIS REST API 2020 — public guest access, no key required
_GENESIS_BASE = "https://www-genesis.destatis.de/genesisWS/rest/2020"
_GUEST = {"username": "GUEST", "password": "GUEST", "language": "de"}

# Table 61243-0001: Erzeugerpreisindizes — Mineralöl und Mineralölerzeugnisse
# Variable CC13-CCNR01 (Heizöl leicht, 50–70 hl, frei Verbrauchsstelle)
# Unit: ct/liter
_DEFAULT_SERIES = settings.destatis_diesel_series  # configurable


class DestatisDieselService:
    """Fetch and cache monthly diesel reference prices from Destatis GENESIS."""

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    # ── public API ─────────────────────────────────────────────────────────

    async def resolve_for_date(
        self,
        db: AsyncSession,
        shipment_date: date,
        lag_months: int = 2,
    ) -> Decimal | None:
        """Return the reference diesel price (ct/liter) applicable for shipment_date.

        Applies the standard 2-month lag: for January shipments, returns the
        November price (published in December).
        """
        ref = _subtract_months(shipment_date, lag_months)
        return await self.fetch_month(db, ref.year, ref.month)

    async def fetch_month(
        self,
        db: AsyncSession,
        year: int,
        month: int,
    ) -> Decimal | None:
        """Return diesel price for given year/month. Hits DB cache, fetches if missing."""
        cached = await self._get_cached(db, year, month)
        if cached is not None:
            return cached

        price = await self._fetch_from_genesis(year, month)
        if price is not None:
            await self._cache(db, year, month, price)
        return price

    async def refresh_recent(self, db: AsyncSession, months: int = 24) -> int:
        """Fetch and cache the last `months` months. Returns count of new rows."""
        today = date.today()
        fetched = 0
        for i in range(months):
            ref = _subtract_months(today, i)
            cached = await self._get_cached(db, ref.year, ref.month)
            if cached is None:
                price = await self._fetch_from_genesis(ref.year, ref.month)
                if price is not None:
                    await self._cache(db, ref.year, ref.month, price)
                    fetched += 1
                await asyncio.sleep(0.3)  # be polite to Destatis
        self.logger.info("destatis_refresh_complete", fetched=fetched, months=months)
        return fetched

    # ── GENESIS API ────────────────────────────────────────────────────────

    async def _fetch_from_genesis(self, year: int, month: int) -> Decimal | None:
        """Call GENESIS timeseries endpoint and parse the ct/liter value."""
        # Period format: YYYYMM (e.g. 202301)
        period = f"{year}{month:02d}"
        params = {
            **_GUEST,
            "name": _DEFAULT_SERIES,
            "startperiod": period,
            "endperiod": period,
            "format": "json",
        }
        url = f"{_GENESIS_BASE}/data/timeseries"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            price = _parse_genesis_response(data)
            if price is not None:
                self.logger.info(
                    "destatis_price_fetched",
                    year=year,
                    month=month,
                    price_ct=float(price),
                    series=_DEFAULT_SERIES,
                )
            else:
                self.logger.warning(
                    "destatis_price_not_found",
                    year=year,
                    month=month,
                    series=_DEFAULT_SERIES,
                )
            return price

        except Exception as exc:
            self.logger.error(
                "destatis_fetch_error",
                year=year,
                month=month,
                error=str(exc),
            )
            return None

    # ── DB cache ───────────────────────────────────────────────────────────

    async def _get_cached(
        self, db: AsyncSession, year: int, month: int
    ) -> Decimal | None:
        row = (
            await db.execute(
                select(DestatisDieselPrice).where(
                    DestatisDieselPrice.price_year == year,
                    DestatisDieselPrice.price_month == month,
                    DestatisDieselPrice.series_code == _DEFAULT_SERIES,
                )
            )
        ).scalar_one_or_none()
        return Decimal(str(row.price_ct)) if row else None

    async def _cache(
        self, db: AsyncSession, year: int, month: int, price_ct: Decimal
    ) -> None:
        stmt = (
            pg_insert(DestatisDieselPrice)
            .values(
                price_year=year,
                price_month=month,
                price_ct=price_ct,
                series_code=_DEFAULT_SERIES,
                fetched_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=["price_year", "price_month", "series_code"],
                set_={"price_ct": price_ct, "fetched_at": datetime.now(UTC)},
            )
        )
        await db.execute(stmt)
        await db.flush()


# ── helpers ────────────────────────────────────────────────────────────────


def _subtract_months(d: date, n: int) -> date:
    month = d.month - n
    year = d.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _parse_genesis_response(data: dict) -> Decimal | None:
    """Extract the ct/liter value from a GENESIS timeseries JSON response.

    GENESIS returns a nested structure:
        data["Object"]["Content"]["Inhalt"]["Daten"][0]["Wert"]
    The value is a string like "148,3" (German decimal notation).
    """
    try:
        # Try the standard GENESIS 2020 REST response structure
        content = data.get("Object", {}).get("Content", {})
        # Navigate to the data rows — structure varies by table
        rows = (
            content.get("Inhalt", {}).get("Daten")
            or content.get("Daten")
            or []
        )
        if rows:
            raw = rows[0].get("Wert") or rows[0].get("wert") or rows[0].get("value")
            if raw:
                return Decimal(str(raw).replace(",", ".").replace(" ", ""))

        # Fallback: search for any numeric "Wert" key recursively
        value = _find_first_wert(data)
        if value is not None:
            return Decimal(str(value).replace(",", ".").replace(" ", ""))

    except Exception as exc:
        logger.warning("genesis_parse_error", error=str(exc))
    return None


def _find_first_wert(obj: object) -> str | None:
    """Recursively find the first 'Wert' value in a nested dict/list."""
    if isinstance(obj, dict):
        for key in ("Wert", "wert", "value", "Value"):
            if key in obj and obj[key] not in (None, "", "-"):
                return obj[key]
        for v in obj.values():
            result = _find_first_wert(v)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_first_wert(item)
            if result is not None:
                return result
    return None


# ── singleton ──────────────────────────────────────────────────────────────

_destatis_service: DestatisDieselService | None = None


def get_destatis_service() -> DestatisDieselService:
    global _destatis_service
    if _destatis_service is None:
        _destatis_service = DestatisDieselService()
    return _destatis_service
