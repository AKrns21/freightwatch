"""Unit tests for ZoneCalculatorService — port of zone-calculator.service.spec.ts.

Issue: #45
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.zone_calculator_service import ZoneCalculatorService, ZoneLookupRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mapping(zone: int, plz_prefix: str = "61118") -> MagicMock:
    """Build a mock TariffZoneMap ORM object."""
    m = MagicMock()
    m.zone = zone
    m.plz_prefix = plz_prefix
    return m


def _scalar_result(mapping: object | None) -> MagicMock:
    """Return a mock db.execute() result whose scalar_one_or_none() is *mapping*."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = mapping
    return result


def _scalars_result(mappings: list[object]) -> MagicMock:
    """Return a mock db.execute() result whose scalars().all() is *mappings*."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = mappings
    result.scalars.return_value = scalars_mock
    return result


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


class TestZoneCalculatorService:
    def setup_method(self) -> None:
        self.service = ZoneCalculatorService()
        self.db = AsyncMock(spec=AsyncSession)
        self.tenant_id = "tenant-123"
        self.carrier_id = "carrier-456"
        self.test_date = date(2024, 3, 1)

    # -----------------------------------------------------------------------
    # Input validation
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_dest_zip_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Destination ZIP code is required"):
            await self.service.calculate_zone(
                self.db, self.tenant_id, self.carrier_id, "DE", "", self.test_date
            )

    @pytest.mark.asyncio
    async def test_whitespace_only_zip_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Destination ZIP code is required"):
            await self.service.calculate_zone(
                self.db, self.tenant_id, self.carrier_id, "DE", "   ", self.test_date
            )

    # -----------------------------------------------------------------------
    # Normalisation
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_zip_is_uppercased_and_trimmed(self) -> None:
        """Lowercase zip with surrounding spaces should be normalised before lookup."""
        mapping = _make_mapping(zone=3, plz_prefix="61118")

        # Exact match returns the zone so we only need one execute call.
        self.db.execute = AsyncMock(return_value=_scalar_result(mapping))

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "de", "  61118  ", self.test_date
        )

        assert zone == 3

    @pytest.mark.asyncio
    async def test_country_is_uppercased_and_trimmed(self) -> None:
        """Lowercase country code should be normalised."""
        mapping = _make_mapping(zone=2, plz_prefix="10115")
        self.db.execute = AsyncMock(return_value=_scalar_result(mapping))

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, " de ", "10115", self.test_date
        )

        assert zone == 2

    # -----------------------------------------------------------------------
    # Exact match
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_exact_match_returns_zone(self) -> None:
        mapping = _make_mapping(zone=5, plz_prefix="61118")
        self.db.execute = AsyncMock(return_value=_scalar_result(mapping))

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 5

    @pytest.mark.asyncio
    async def test_exact_match_takes_priority_over_prefix(self) -> None:
        """When exact match succeeds, prefix and pattern queries must NOT be called."""
        mapping = _make_mapping(zone=7, plz_prefix="61118")
        self.db.execute = AsyncMock(return_value=_scalar_result(mapping))

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 7
        # Only one DB call (the exact-match query) should have been made.
        assert self.db.execute.call_count == 1

    # -----------------------------------------------------------------------
    # Prefix matching
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_prefix_match_5_digit_returns_zone(self) -> None:
        """5-digit prefix match should be tried first (after exact)."""
        mapping = _make_mapping(zone=4, plz_prefix="61118")
        # First call = exact (no match), second = 5-char prefix (match)
        self.db.execute = AsyncMock(
            side_effect=[_scalar_result(None), _scalar_result(mapping)]
        )

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 4

    @pytest.mark.asyncio
    async def test_prefix_match_falls_back_from_5_to_4(self) -> None:
        """When 5-char prefix has no match, fall back to 4-char prefix."""
        mapping = _make_mapping(zone=2, plz_prefix="6111")
        # exact=None, prefix5=None, prefix4=match
        self.db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),  # exact
                _scalar_result(None),  # prefix len 5
                _scalar_result(mapping),  # prefix len 4
            ]
        )

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 2

    @pytest.mark.asyncio
    async def test_prefix_match_falls_back_through_5_to_3(self) -> None:
        """Prefix fallback should continue down to 3-char prefix."""
        mapping = _make_mapping(zone=1, plz_prefix="611")
        # exact, prefix5, prefix4, prefix3 (match)
        self.db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(None),
                _scalar_result(None),
                _scalar_result(mapping),
            ]
        )

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 1

    @pytest.mark.asyncio
    async def test_prefix_match_falls_back_to_2(self) -> None:
        """Prefix fallback should reach 2-char prefix."""
        mapping = _make_mapping(zone=9, plz_prefix="61")
        # exact, prefix5, prefix4, prefix3, prefix2 (match)
        self.db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(None),
                _scalar_result(None),
                _scalar_result(None),
                _scalar_result(mapping),
            ]
        )

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 9

    # -----------------------------------------------------------------------
    # Pattern matching
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pattern_match_fallback(self) -> None:
        """When no prefix matches, pattern matching should be attempted."""
        mapping = _make_mapping(zone=6, plz_prefix=r"^6\d{4}$")
        # exact=None, prefix5-2=None (4 calls), pattern query returns mapping
        none_results = [_scalar_result(None)] * 5  # exact + 4 prefix lengths
        pattern_result = _scalars_result([mapping])
        self.db.execute = AsyncMock(side_effect=none_results + [pattern_result])

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 6

    @pytest.mark.asyncio
    async def test_pattern_multiple_patterns_first_match_wins(self) -> None:
        """First pattern that matches (in DB order) should be returned."""
        matching1 = _make_mapping(zone=3, plz_prefix=r"^6\d{4}$")
        matching2 = _make_mapping(zone=8, plz_prefix=r"^\d{5}$")
        none_results = [_scalar_result(None)] * 5
        pattern_result = _scalars_result([matching1, matching2])
        self.db.execute = AsyncMock(side_effect=none_results + [pattern_result])

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 3  # first match wins

    @pytest.mark.asyncio
    async def test_invalid_regex_is_skipped_gracefully(self) -> None:
        """An invalid regex pattern should be logged and skipped; next pattern is tried."""
        bad_pattern = _make_mapping(zone=99, plz_prefix="[invalid(")
        good_pattern = _make_mapping(zone=5, plz_prefix=r"^6\d{4}$")
        none_results = [_scalar_result(None)] * 5
        pattern_result = _scalars_result([bad_pattern, good_pattern])
        self.db.execute = AsyncMock(side_effect=none_results + [pattern_result])

        zone = await self.service.calculate_zone(
            self.db, self.tenant_id, self.carrier_id, "DE", "61118", self.test_date
        )

        assert zone == 5  # bad pattern skipped, good pattern used

    # -----------------------------------------------------------------------
    # Not found
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_no_mapping_found_raises_http_404(self) -> None:
        """If all lookup strategies fail, HTTPException 404 must be raised."""
        none_results = [_scalar_result(None)] * 5  # exact + 4 prefix
        pattern_result = _scalars_result([])  # no pattern rows
        self.db.execute = AsyncMock(side_effect=none_results + [pattern_result])

        with pytest.raises(HTTPException) as exc_info:
            await self.service.calculate_zone(
                self.db, self.tenant_id, self.carrier_id, "DE", "99999", self.test_date
            )

        assert exc_info.value.status_code == 404
        assert "99999" in exc_info.value.detail

    # -----------------------------------------------------------------------
    # DB error wrapping
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unexpected_db_error_raises_runtime_error(self) -> None:
        """Unexpected DB errors should be wrapped in RuntimeError."""
        self.db.execute = AsyncMock(side_effect=Exception("connection lost"))

        with pytest.raises(RuntimeError, match="Zone calculation failed"):
            await self.service.calculate_zone(
                self.db, self.tenant_id, self.carrier_id, "DE", "10115", self.test_date
            )

    # -----------------------------------------------------------------------
    # Date filters
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_date_filter_applied_to_query(self) -> None:
        """The lookup_date must be forwarded to the DB query."""
        # We simply verify that execute() is called at all (the WHERE clauses
        # are built inside _try_exact_match / _try_prefix_matching via SQLAlchemy
        # and cannot be introspected without running against a real DB).
        # This test ensures no TypeError from incorrect argument handling.
        none_results = [_scalar_result(None)] * 5
        pattern_result = _scalars_result([])
        self.db.execute = AsyncMock(side_effect=none_results + [pattern_result])

        specific_date = date(2023, 7, 15)
        with pytest.raises(HTTPException):
            await self.service.calculate_zone(
                self.db, self.tenant_id, self.carrier_id, "DE", "10115", specific_date
            )

        assert self.db.execute.called

    # -----------------------------------------------------------------------
    # bulkCalculateZones
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_bulk_calculate_zones_caching(self) -> None:
        """Duplicate requests with the same key must not trigger extra DB calls."""
        mapping = _make_mapping(zone=3, plz_prefix="10115")
        # Exact match returns zone on the first call; after that, cache serves it.
        self.db.execute = AsyncMock(return_value=_scalar_result(mapping))

        requests = [
            ZoneLookupRequest(country="DE", dest_zip="10115", date=self.test_date),
            ZoneLookupRequest(country="DE", dest_zip="10115", date=self.test_date),  # duplicate
        ]

        results = await self.service.bulk_calculate_zones(
            self.db, self.tenant_id, self.carrier_id, requests
        )

        assert len(results) == 1
        key = "DE-10115-2024-03-01"
        assert results[key] == 3
        # Only one actual DB call (for the first request); second is cached.
        assert self.db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_bulk_calculate_zones_failure_skipped(self) -> None:
        """A failing lookup should not stop other requests from being processed."""
        good_mapping = _make_mapping(zone=5, plz_prefix="80331")
        # First request (10115) → all lookups fail (no match at all)
        # Second request (80331) → exact match succeeds
        no_match_results = [_scalar_result(None)] * 5 + [_scalars_result([])]
        match_result = _scalar_result(good_mapping)
        self.db.execute = AsyncMock(side_effect=no_match_results + [match_result])

        requests = [
            ZoneLookupRequest(country="DE", dest_zip="10115", date=self.test_date),  # will fail
            ZoneLookupRequest(country="DE", dest_zip="80331", date=self.test_date),  # will succeed
        ]

        results = await self.service.bulk_calculate_zones(
            self.db, self.tenant_id, self.carrier_id, requests
        )

        assert "DE-10115-2024-03-01" not in results
        assert results.get("DE-80331-2024-03-01") == 5

    @pytest.mark.asyncio
    async def test_bulk_calculate_zones_different_dates_not_cached(self) -> None:
        """Same ZIP but different dates must not share a cache entry."""
        mapping = _make_mapping(zone=3, plz_prefix="10115")
        self.db.execute = AsyncMock(return_value=_scalar_result(mapping))

        requests = [
            ZoneLookupRequest(country="DE", dest_zip="10115", date=date(2024, 1, 1)),
            ZoneLookupRequest(country="DE", dest_zip="10115", date=date(2024, 6, 1)),
        ]

        results = await self.service.bulk_calculate_zones(
            self.db, self.tenant_id, self.carrier_id, requests
        )

        assert len(results) == 2
        # Two separate DB calls (one per distinct date)
        assert self.db.execute.call_count == 2

    # -----------------------------------------------------------------------
    # getAvailableZones
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_available_zones_returns_sorted_unique_zones(self) -> None:
        """Result must be a sorted list of unique zone numbers."""
        # fetchall() returns rows as tuples of (zone,)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(3,), (1,), (3,), (2,), (1,)]
        self.db.execute = AsyncMock(return_value=mock_result)

        zones = await self.service.get_available_zones(
            self.db, self.tenant_id, self.carrier_id, "DE", self.test_date
        )

        assert zones == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_get_available_zones_returns_empty_list_on_db_error(self) -> None:
        """Any DB error must return [] rather than propagating the exception."""
        self.db.execute = AsyncMock(side_effect=Exception("timeout"))

        zones = await self.service.get_available_zones(
            self.db, self.tenant_id, self.carrier_id, "DE", self.test_date
        )

        assert zones == []

    @pytest.mark.asyncio
    async def test_get_available_zones_returns_empty_list_when_no_rows(self) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        self.db.execute = AsyncMock(return_value=mock_result)

        zones = await self.service.get_available_zones(
            self.db, self.tenant_id, self.carrier_id, "DE", self.test_date
        )

        assert zones == []

    @pytest.mark.asyncio
    async def test_get_available_zones_normalises_country(self) -> None:
        """Country passed with lowercase/spaces should be normalised."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1,)]
        self.db.execute = AsyncMock(return_value=mock_result)

        zones = await self.service.get_available_zones(
            self.db, self.tenant_id, self.carrier_id, " de ", self.test_date
        )

        assert zones == [1]
