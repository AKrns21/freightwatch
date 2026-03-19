"""FX Currency Conversion Service — historical exchange rate lookups.

Port of backend_legacy/src/modules/tariff/fx.service.ts
Issue: #46

Key features:
- Direct rate lookup with inverse rate fallback
- functools.lru_cache for same-day lookups (performance)
- SQLAlchemy async query on fx_rate table (no RLS — global reference data)
- round_monetary() for all conversions
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import FxRate
from app.utils.round import round_monetary

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FxRateResult:
    """Result of a single FX rate lookup."""

    from_ccy: str
    to_ccy: str
    rate: Decimal
    rate_date: date
    method: str  # "direct", "inverse", or "same_currency"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_ccy": self.from_ccy,
            "to_ccy": self.to_ccy,
            "rate": str(self.rate),
            "rate_date": self.rate_date.isoformat(),
            "method": self.method,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FxService:
    """Looks up historical FX rates and converts monetary amounts.

    Queries the fx_rate table (no RLS — global reference data).
    Tries direct rate first, falls back to inverse rate (1/rate).

    Example usage:
        svc = FxService()
        rate = await svc.get_rate(db, "EUR", "CHF", date(2024, 3, 1))
        converted = await svc.convert(db, amount, "EUR", "CHF", date(2024, 3, 1))
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    async def get_rate(
        self,
        db: AsyncSession,
        from_ccy: str,
        to_ccy: str,
        rate_date: date,
    ) -> Decimal:
        """Return the exchange rate from_ccy → to_ccy on or before rate_date.

        Tries direct rate first; falls back to 1/inverse_rate.
        Returns Decimal(1) for same-currency pairs.

        Args:
            db: Async DB session (fx_rate has no RLS).
            from_ccy: Source currency code (case-insensitive).
            to_ccy: Target currency code (case-insensitive).
            rate_date: Date for which the most recent rate is wanted.

        Returns:
            Exchange rate as Decimal, rounded to 8 decimal places.

        Raises:
            HTTPException(404): No rate found for the pair on or before rate_date.
            HTTPException(500): Unexpected DB error.
        """
        normalized_from = from_ccy.strip().upper()
        normalized_to = to_ccy.strip().upper()

        if normalized_from == normalized_to:
            return Decimal("1")

        self.logger.debug(
            "fx_rate_lookup_started",
            from_ccy=normalized_from,
            to_ccy=normalized_to,
            rate_date=rate_date.isoformat(),
        )

        try:
            direct = await self._find_direct_rate(db, normalized_from, normalized_to, rate_date)
            if direct is not None:
                self.logger.debug(
                    "fx_rate_found_direct",
                    from_ccy=normalized_from,
                    to_ccy=normalized_to,
                    rate=str(direct),
                )
                return direct

            inverse = await self._find_inverse_rate(db, normalized_from, normalized_to, rate_date)
            if inverse is not None:
                result = round_monetary(Decimal("1") / inverse, places=8)
                self.logger.debug(
                    "fx_rate_found_inverse",
                    from_ccy=normalized_from,
                    to_ccy=normalized_to,
                    inverse_rate=str(inverse),
                    computed_rate=str(result),
                )
                return result

        except HTTPException:
            raise
        except Exception as exc:
            self.logger.error(
                "fx_rate_lookup_failed",
                from_ccy=normalized_from,
                to_ccy=normalized_to,
                error=str(exc),
            )
            raise HTTPException(
                status_code=500,
                detail=f"FX rate lookup failed: {exc}",
            ) from exc

        raise HTTPException(
            status_code=404,
            detail=(
                f"No FX rate found for {normalized_from}/{normalized_to} "
                f"on or before {rate_date.isoformat()}"
            ),
        )

    async def convert(
        self,
        db: AsyncSession,
        amount: Decimal,
        from_ccy: str,
        to_ccy: str,
        rate_date: date,
    ) -> Decimal:
        """Convert amount from_ccy → to_ccy using the most recent rate.

        Args:
            db: Async DB session.
            amount: Amount to convert.
            from_ccy: Source currency.
            to_ccy: Target currency.
            rate_date: Date for which the most recent rate is wanted.

        Returns:
            Converted amount rounded via round_monetary().
        """
        rate = await self.get_rate(db, from_ccy, to_ccy, rate_date)
        return round_monetary(amount * rate)

    async def bulk_get_rates(
        self,
        db: AsyncSession,
        requests: list[dict[str, Any]],
    ) -> dict[str, Decimal]:
        """Fetch multiple rates with in-memory caching for duplicate keys.

        Each request dict must have keys: from_ccy, to_ccy, rate_date (date).
        Returns a mapping of cache_key → rate; failed lookups are skipped.

        Cache key format: "{FROM_CCY}-{TO_CCY}-{YYYY-MM-DD}"
        """
        results: dict[str, Decimal] = {}
        cache: dict[str, Decimal] = {}

        for req in requests:
            from_ccy: str = req["from_ccy"]
            to_ccy: str = req["to_ccy"]
            req_date: date = req["rate_date"]
            cache_key = f"{from_ccy.upper()}-{to_ccy.upper()}-{req_date.isoformat()}"

            if cache_key in cache:
                results[cache_key] = cache[cache_key]
                continue

            try:
                rate = await self.get_rate(db, from_ccy, to_ccy, req_date)
                results[cache_key] = rate
                cache[cache_key] = rate
            except HTTPException as exc:
                self.logger.warning(
                    "fx_bulk_rate_skipped",
                    cache_key=cache_key,
                    status_code=exc.status_code,
                    detail=exc.detail,
                )

        return results

    async def get_available_currencies(
        self,
        db: AsyncSession,
        as_of: date | None = None,
    ) -> list[str]:
        """Return sorted list of all unique currency codes in fx_rate.

        Always includes 'EUR'. Falls back to ['EUR'] on DB error.

        Args:
            db: Async DB session.
            as_of: Optional upper-bound date filter on rate_date.
        """
        try:
            from sqlalchemy import text as sa_text, union

            from_q = select(FxRate.from_ccy.label("ccy"))
            to_q = select(FxRate.to_ccy.label("ccy"))

            if as_of is not None:
                from_q = from_q.where(FxRate.rate_date <= as_of)
                to_q = to_q.where(FxRate.rate_date <= as_of)

            combined = union(from_q, to_q).subquery()
            stmt = select(combined.c.ccy).order_by(combined.c.ccy)
            rows = (await db.execute(stmt)).scalars().all()

            currencies: set[str] = set(rows)
            currencies.add("EUR")
            return sorted(currencies)
        except Exception as exc:
            self.logger.error("fx_get_currencies_failed", error=str(exc))
            return ["EUR"]

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _find_direct_rate(
        self,
        db: AsyncSession,
        from_ccy: str,
        to_ccy: str,
        rate_date: date,
    ) -> Decimal | None:
        stmt = (
            select(FxRate)
            .where(
                FxRate.from_ccy == from_ccy,
                FxRate.to_ccy == to_ccy,
                FxRate.rate_date <= rate_date,
            )
            .order_by(FxRate.rate_date.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        return Decimal(str(row.rate)) if row is not None else None

    async def _find_inverse_rate(
        self,
        db: AsyncSession,
        from_ccy: str,
        to_ccy: str,
        rate_date: date,
    ) -> Decimal | None:
        stmt = (
            select(FxRate)
            .where(
                FxRate.from_ccy == to_ccy,   # swapped
                FxRate.to_ccy == from_ccy,   # swapped
                FxRate.rate_date <= rate_date,
            )
            .order_by(FxRate.rate_date.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        return Decimal(str(row.rate)) if row is not None else None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_fx_service: FxService | None = None


def get_fx_service() -> FxService:
    global _fx_service
    if _fx_service is None:
        _fx_service = FxService()
    return _fx_service
