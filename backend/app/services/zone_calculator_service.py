"""Zone Calculator Service — determines freight zone from PLZ and carrier zone maps.

Port of backend_legacy/src/modules/tariff/zone-calculator.service.ts
Issue: #45
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import TariffTable, TariffZoneMap

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / result types
# ---------------------------------------------------------------------------


@dataclass
class ZoneLookupRequest:
    """A single zone-lookup request used by bulkCalculateZones."""

    country: str
    dest_zip: str
    date: date


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ZoneCalculatorService:
    """Resolves the freight zone number for a destination ZIP code.

    Matching priority (highest to lowest):
      1. Exact match  (match_type = 'exact')
      2. Prefix match (match_type = 'prefix', lengths 5 → 4 → 3 → 2)
      3. Pattern match (match_type = 'pattern', Python regex)

    Tenant / carrier scoping and temporal validity are applied on every query
    by joining TariffZoneMap through TariffTable.
    """

    def __init__(self) -> None:
        self.logger = logger.bind(service="ZoneCalculatorService")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def calculate_zone(
        self,
        db: AsyncSession,
        tenant_id: UUID | str,
        carrier_id: UUID | str,
        country: str,
        dest_zip: str,
        lookup_date: date,
    ) -> int:
        """Determine the freight zone for a destination ZIP code.

        Args:
            db: Async SQLAlchemy session (tenant context must already be set
                via ``SET LOCAL app.current_tenant``).
            tenant_id: Tenant UUID.
            carrier_id: Carrier UUID.
            country: 2-char ISO country code (case-insensitive).
            dest_zip: Destination postal code (trimmed + uppercased internally).
            lookup_date: Date used to filter valid tariff tables.

        Returns:
            Integer zone number.

        Raises:
            ValueError: If *dest_zip* is empty or whitespace-only.
            HTTPException(404): If no zone mapping matches.
            RuntimeError: If an unexpected database error occurs.
        """
        if not dest_zip or not dest_zip.strip():
            raise ValueError("Destination ZIP code is required for zone calculation")

        normalized_zip = dest_zip.strip().upper()
        normalized_country = country.strip().upper()

        self.logger.info(
            "zone_calculation_started",
            tenant_id=str(tenant_id),
            carrier_id=str(carrier_id),
            country=normalized_country,
            dest_zip=normalized_zip,
        )

        try:
            # 1. Exact match
            zone = await self._try_exact_match(
                db, tenant_id, carrier_id, normalized_country, normalized_zip, lookup_date
            )
            if zone is not None:
                self.logger.info(
                    "zone_calculation_completed",
                    zone=zone,
                    method="exact",
                    country=normalized_country,
                    dest_zip=normalized_zip,
                )
                return zone

            # 2. Prefix match (5 → 4 → 3 → 2)
            zone = await self._try_prefix_matching(
                db, tenant_id, carrier_id, normalized_country, normalized_zip, lookup_date
            )
            if zone is not None:
                self.logger.info(
                    "zone_calculation_completed",
                    zone=zone,
                    method="prefix",
                    country=normalized_country,
                    dest_zip=normalized_zip,
                )
                return zone

            # 3. Pattern match (regex)
            zone = await self._try_pattern_matching(
                db, tenant_id, carrier_id, normalized_country, normalized_zip, lookup_date
            )
            if zone is not None:
                self.logger.info(
                    "zone_calculation_completed",
                    zone=zone,
                    method="pattern",
                    country=normalized_country,
                    dest_zip=normalized_zip,
                )
                return zone

            raise HTTPException(
                status_code=404,
                detail=(
                    f"No zone mapping found for carrier {carrier_id}, "
                    f"country {normalized_country}, ZIP {normalized_zip}"
                ),
            )

        except (HTTPException, ValueError):
            raise
        except Exception as e:
            self.logger.error(
                "zone_calculation_error",
                error=str(e),
                country=normalized_country,
                dest_zip=normalized_zip,
            )
            raise RuntimeError(f"Zone calculation failed: {e}") from e

    async def bulk_calculate_zones(
        self,
        db: AsyncSession,
        tenant_id: UUID | str,
        carrier_id: UUID | str,
        requests: list[ZoneLookupRequest],
    ) -> dict[str, int]:
        """Calculate zones for multiple ZIP codes, with per-request caching.

        Duplicate requests (same country + ZIP + date) are served from an
        in-memory cache so the database is only hit once per unique combination.
        Failures for individual requests are logged and skipped; other results
        are still returned.

        Args:
            db: Async SQLAlchemy session.
            tenant_id: Tenant UUID.
            carrier_id: Carrier UUID.
            requests: List of :class:`ZoneLookupRequest` items.

        Returns:
            Dict mapping ``"{country}-{dest_zip}-{date_iso}"`` cache keys to
            resolved zone numbers.  Failed lookups are absent from the result.
        """
        results: dict[str, int] = {}
        cache: dict[str, int] = {}

        for req in requests:
            cache_key = f"{req.country.strip().upper()}-{req.dest_zip.strip().upper()}-{req.date.isoformat()}"

            if cache_key in cache:
                results[cache_key] = cache[cache_key]
                continue

            try:
                zone = await self.calculate_zone(
                    db,
                    tenant_id,
                    carrier_id,
                    req.country,
                    req.dest_zip,
                    req.date,
                )
                results[cache_key] = zone
                cache[cache_key] = zone
            except Exception as e:
                self.logger.warning(
                    "bulk_zone_lookup_failed",
                    cache_key=cache_key,
                    error=str(e),
                )
                # Continue processing remaining requests

        return results

    async def get_available_zones(
        self,
        db: AsyncSession,
        tenant_id: UUID | str,
        carrier_id: UUID | str,
        country: str,
        lookup_date: date,
    ) -> list[int]:
        """Return all unique zone numbers available for a carrier + country + date.

        Args:
            db: Async SQLAlchemy session.
            tenant_id: Tenant UUID.
            carrier_id: Carrier UUID.
            country: 2-char ISO country code (case-insensitive).
            lookup_date: Date used to filter valid tariff tables.

        Returns:
            Sorted list of unique zone integers.  Returns ``[]`` on any error
            so callers can degrade gracefully.
        """
        normalized_country = country.strip().upper()
        try:
            stmt = (
                select(TariffZoneMap.zone)
                .join(TariffTable, TariffZoneMap.tariff_table_id == TariffTable.id)
                .where(
                    TariffTable.tenant_id == tenant_id,
                    TariffTable.carrier_id == carrier_id,
                    TariffZoneMap.country_code == normalized_country,
                    TariffTable.valid_from <= lookup_date,
                    or_(
                        TariffTable.valid_until.is_(None),
                        TariffTable.valid_until >= lookup_date,
                    ),
                )
                .distinct()
                .order_by(TariffZoneMap.zone)
            )
            result = await db.execute(stmt)
            zones = sorted({row[0] for row in result.fetchall()})
            self.logger.debug(
                "available_zones_fetched",
                carrier_id=str(carrier_id),
                country=normalized_country,
                zones=zones,
            )
            return zones
        except Exception as e:
            self.logger.error(
                "get_available_zones_error",
                carrier_id=str(carrier_id),
                country=normalized_country,
                error=str(e),
            )
            return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _try_exact_match(
        self,
        db: AsyncSession,
        tenant_id: UUID | str,
        carrier_id: UUID | str,
        country: str,
        dest_zip: str,
        lookup_date: date,
    ) -> int | None:
        """Look up an exact match (match_type = 'exact') for the full ZIP code.

        Args:
            db: Async SQLAlchemy session.
            tenant_id: Tenant UUID.
            carrier_id: Carrier UUID.
            country: Normalised uppercase 2-char country code.
            dest_zip: Normalised uppercase ZIP code.
            lookup_date: Effective date for tariff validity.

        Returns:
            Zone integer if found, ``None`` otherwise.
        """
        stmt = (
            select(TariffZoneMap)
            .join(TariffTable, TariffZoneMap.tariff_table_id == TariffTable.id)
            .where(
                TariffTable.tenant_id == tenant_id,
                TariffTable.carrier_id == carrier_id,
                TariffZoneMap.country_code == country,
                TariffZoneMap.match_type == "exact",
                TariffZoneMap.plz_prefix == dest_zip,
                TariffTable.valid_from <= lookup_date,
                or_(
                    TariffTable.valid_until.is_(None),
                    TariffTable.valid_until >= lookup_date,
                ),
            )
            .order_by(TariffTable.valid_from.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        mapping = result.scalar_one_or_none()
        if mapping is not None:
            self.logger.debug(
                "zone_exact_match_found",
                zip=dest_zip,
                zone=mapping.zone,
            )
            return mapping.zone
        return None

    async def _try_prefix_matching(
        self,
        db: AsyncSession,
        tenant_id: UUID | str,
        carrier_id: UUID | str,
        country: str,
        dest_zip: str,
        lookup_date: date,
    ) -> int | None:
        """Attempt prefix matching from longest (5) down to shortest (2) prefix.

        Args:
            db: Async SQLAlchemy session.
            tenant_id: Tenant UUID.
            carrier_id: Carrier UUID.
            country: Normalised uppercase 2-char country code.
            dest_zip: Normalised uppercase ZIP code.
            lookup_date: Effective date for tariff validity.

        Returns:
            Zone integer for the first (longest) matching prefix, or ``None``.
        """
        for prefix_len in range(min(5, len(dest_zip)), 1, -1):
            prefix = dest_zip[:prefix_len]
            stmt = (
                select(TariffZoneMap)
                .join(TariffTable, TariffZoneMap.tariff_table_id == TariffTable.id)
                .where(
                    TariffTable.tenant_id == tenant_id,
                    TariffTable.carrier_id == carrier_id,
                    TariffZoneMap.country_code == country,
                    TariffZoneMap.match_type == "prefix",
                    TariffZoneMap.plz_prefix == prefix,
                    TariffTable.valid_from <= lookup_date,
                    or_(
                        TariffTable.valid_until.is_(None),
                        TariffTable.valid_until >= lookup_date,
                    ),
                )
                .order_by(TariffTable.valid_from.desc())
                .limit(1)
            )
            result = await db.execute(stmt)
            mapping = result.scalar_one_or_none()
            if mapping is not None:
                self.logger.debug(
                    "zone_prefix_match_found",
                    prefix=prefix,
                    prefix_len=len(prefix),
                    zone=mapping.zone,
                )
                return mapping.zone
        return None

    async def _try_pattern_matching(
        self,
        db: AsyncSession,
        tenant_id: UUID | str,
        carrier_id: UUID | str,
        country: str,
        dest_zip: str,
        lookup_date: date,
    ) -> int | None:
        """Fetch all pattern-type mappings and test each as a Python regex.

        Patterns with invalid regex syntax are logged and skipped gracefully.
        The first matching pattern (ordered by tariff valid_from DESC) wins.

        Args:
            db: Async SQLAlchemy session.
            tenant_id: Tenant UUID.
            carrier_id: Carrier UUID.
            country: Normalised uppercase 2-char country code.
            dest_zip: Normalised uppercase ZIP code.
            lookup_date: Effective date for tariff validity.

        Returns:
            Zone integer for the first matching pattern, or ``None``.
        """
        stmt = (
            select(TariffZoneMap)
            .join(TariffTable, TariffZoneMap.tariff_table_id == TariffTable.id)
            .where(
                TariffTable.tenant_id == tenant_id,
                TariffTable.carrier_id == carrier_id,
                TariffZoneMap.country_code == country,
                TariffZoneMap.match_type == "pattern",
                TariffTable.valid_from <= lookup_date,
                or_(
                    TariffTable.valid_until.is_(None),
                    TariffTable.valid_until >= lookup_date,
                ),
            )
            .order_by(TariffTable.valid_from.desc())
        )
        result = await db.execute(stmt)
        mappings = result.scalars().all()

        for mapping in mappings:
            pattern = mapping.plz_prefix
            try:
                if re.search(pattern, dest_zip, re.IGNORECASE):
                    self.logger.debug(
                        "zone_pattern_match_found",
                        pattern=pattern,
                        zone=mapping.zone,
                    )
                    return mapping.zone
            except re.error as e:
                self.logger.warning(
                    "zone_pattern_invalid_regex",
                    pattern=pattern,
                    error=str(e),
                )

        return None
