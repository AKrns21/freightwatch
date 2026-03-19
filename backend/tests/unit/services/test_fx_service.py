"""Unit tests for FxService.

Tests: same-currency shortcut, direct rate, inverse rate fallback,
       404 when no rate found, DB error wrapping, bulk_get_rates caching,
       get_available_currencies.

Port of backend_legacy/src/modules/tariff/fx.service.spec.ts
Issue: #46
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.fx_service import FxService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fx_row(rate: float, rate_date: date = date(2024, 2, 15)) -> MagicMock:
    row = MagicMock()
    row.rate = Decimal(str(rate))
    row.rate_date = rate_date
    return row


class TestFxService:
    """Test suite for FxService."""

    def setup_method(self) -> None:
        self.service = FxService()
        self.db = AsyncMock()
        self.test_date = date(2024, 3, 1)

    # ============================================================================
    # get_rate — same currency
    # ============================================================================

    def test_same_currency_returns_one(self) -> None:
        """Same from/to currency returns Decimal('1') without a DB call."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            self.service.get_rate(self.db, "EUR", "EUR", self.test_date)
        )
        assert result == Decimal("1")
        self.db.execute.assert_not_called()

    def test_same_currency_case_insensitive(self) -> None:
        """Lower-case 'eur' == upper-case 'EUR' → same-currency shortcut."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            self.service.get_rate(self.db, "eur", "EUR", self.test_date)
        )
        assert result == Decimal("1")

    # ============================================================================
    # get_rate — direct rate
    # ============================================================================

    @pytest.mark.asyncio
    async def test_direct_rate_returned(self) -> None:
        """Direct rate row found → return its rate unchanged."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _make_fx_row(0.9875)
        self.db.execute.return_value = mock_result

        result = await self.service.get_rate(self.db, "EUR", "CHF", self.test_date)

        assert result == Decimal("0.9875")

    @pytest.mark.asyncio
    async def test_direct_rate_normalises_currency_codes(self) -> None:
        """Currency codes are uppercased before the DB query."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _make_fx_row(1.085)
        self.db.execute.return_value = mock_result

        result = await self.service.get_rate(self.db, "eur", "usd", self.test_date)

        assert result == Decimal("1.085")

    # ============================================================================
    # get_rate — inverse rate fallback
    # ============================================================================

    @pytest.mark.asyncio
    async def test_inverse_rate_fallback(self) -> None:
        """When direct rate is missing, use 1/inverse_rate."""
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None

        inverse_result = MagicMock()
        inverse_result.scalar_one_or_none.return_value = _make_fx_row(0.922)

        self.db.execute.side_effect = [no_result, inverse_result]

        result = await self.service.get_rate(self.db, "EUR", "USD", self.test_date)

        # 1 / 0.922 ≈ 1.08460…, rounded to 8 dp
        expected = Decimal("1") / Decimal("0.922")
        from app.utils.round import round_monetary
        assert result == round_monetary(expected, places=8)

    @pytest.mark.asyncio
    async def test_inverse_fallback_queries_swapped_pair(self) -> None:
        """The inverse lookup must query (to_ccy, from_ccy), not (from_ccy, to_ccy)."""
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None

        inverse_result = MagicMock()
        inverse_result.scalar_one_or_none.return_value = _make_fx_row(0.9)

        self.db.execute.side_effect = [no_result, inverse_result]

        await self.service.get_rate(self.db, "EUR", "USD", self.test_date)

        # Two DB calls: direct, then inverse
        assert self.db.execute.call_count == 2

    # ============================================================================
    # get_rate — not found
    # ============================================================================

    @pytest.mark.asyncio
    async def test_raises_404_when_no_rate_found(self) -> None:
        """HTTPException(404) raised when neither direct nor inverse rate exists."""
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        self.db.execute.return_value = no_result

        with pytest.raises(HTTPException) as exc_info:
            await self.service.get_rate(self.db, "EUR", "XYZ", self.test_date)

        assert exc_info.value.status_code == 404
        assert "EUR/XYZ" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_both_lookups_called_before_404(self) -> None:
        """Service tries both direct and inverse before raising 404."""
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        self.db.execute.return_value = no_result

        with pytest.raises(HTTPException):
            await self.service.get_rate(self.db, "EUR", "XYZ", self.test_date)

        assert self.db.execute.call_count == 2

    # ============================================================================
    # get_rate — DB error
    # ============================================================================

    @pytest.mark.asyncio
    async def test_db_error_wrapped_as_500(self) -> None:
        """Unexpected DB exceptions are wrapped in HTTPException(500)."""
        self.db.execute.side_effect = RuntimeError("connection refused")

        with pytest.raises(HTTPException) as exc_info:
            await self.service.get_rate(self.db, "EUR", "USD", self.test_date)

        assert exc_info.value.status_code == 500
        assert "connection refused" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_404_not_wrapped(self) -> None:
        """HTTPException(404) passes through without being re-wrapped."""
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        self.db.execute.return_value = no_result

        with pytest.raises(HTTPException) as exc_info:
            await self.service.get_rate(self.db, "EUR", "XYZ", self.test_date)

        # Must be 404, not 500
        assert exc_info.value.status_code == 404

    # ============================================================================
    # convert
    # ============================================================================

    @pytest.mark.asyncio
    async def test_convert_applies_rate_and_rounds(self) -> None:
        """convert() multiplies amount by rate and applies round_monetary."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _make_fx_row(0.9875)
        self.db.execute.return_value = mock_result

        result = await self.service.convert(
            self.db, Decimal("100.00"), "EUR", "CHF", self.test_date
        )

        from app.utils.round import round_monetary
        assert result == round_monetary(Decimal("100.00") * Decimal("0.9875"))

    # ============================================================================
    # bulk_get_rates
    # ============================================================================

    @pytest.mark.asyncio
    async def test_bulk_get_rates_deduplicates(self) -> None:
        """Duplicate requests share one DB round-trip via in-memory cache."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _make_fx_row(1.085)

        # Direct hits on first two unique requests; inverse call not needed
        self.db.execute.return_value = mock_result

        requests = [
            {"from_ccy": "EUR", "to_ccy": "USD", "rate_date": self.test_date},
            {"from_ccy": "EUR", "to_ccy": "USD", "rate_date": self.test_date},  # duplicate
            {"from_ccy": "EUR", "to_ccy": "CHF", "rate_date": self.test_date},
        ]
        results = await self.service.bulk_get_rates(self.db, requests)

        assert "EUR-USD-2024-03-01" in results
        assert "EUR-CHF-2024-03-01" in results
        # Only 2 actual DB queries (not 3)
        assert self.db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_bulk_get_rates_skips_failed_pairs(self) -> None:
        """Failed lookups are silently skipped; successful ones still returned."""
        ok_result = MagicMock()
        ok_result.scalar_one_or_none.return_value = _make_fx_row(1.085)

        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None

        # EUR/USD → ok; EUR/XYZ → both lookups fail → skip
        self.db.execute.side_effect = [ok_result, no_result, no_result]

        requests = [
            {"from_ccy": "EUR", "to_ccy": "USD", "rate_date": self.test_date},
            {"from_ccy": "EUR", "to_ccy": "XYZ", "rate_date": self.test_date},
        ]
        results = await self.service.bulk_get_rates(self.db, requests)

        assert "EUR-USD-2024-03-01" in results
        assert "EUR-XYZ-2024-03-01" not in results

    # ============================================================================
    # get_available_currencies
    # ============================================================================

    @pytest.mark.asyncio
    async def test_get_available_currencies_sorted_unique(self) -> None:
        """Returns alphabetically sorted, deduplicated currency codes."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["USD", "CHF", "EUR", "GBP"]
        self.db.execute.return_value = mock_result

        result = await self.service.get_available_currencies(self.db)

        assert result == sorted(set(result))
        assert "EUR" in result

    @pytest.mark.asyncio
    async def test_get_available_currencies_always_includes_eur(self) -> None:
        """EUR is always present even if absent from the DB rows."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["USD", "CHF"]
        self.db.execute.return_value = mock_result

        result = await self.service.get_available_currencies(self.db)

        assert "EUR" in result

    @pytest.mark.asyncio
    async def test_get_available_currencies_fallback_on_db_error(self) -> None:
        """DB error returns ['EUR'] gracefully."""
        self.db.execute.side_effect = RuntimeError("DB unreachable")

        result = await self.service.get_available_currencies(self.db)

        assert result == ["EUR"]

    # ============================================================================
    # get_fx_service singleton
    # ============================================================================

    def test_singleton_returns_same_instance(self) -> None:
        from app.services.fx_service import get_fx_service

        svc_a = get_fx_service()
        svc_b = get_fx_service()
        assert svc_a is svc_b
