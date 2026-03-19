"""Unit tests for BenchmarkService.

Tests: bulk processing with semaphore, error isolation, progress callback,
       missing shipment handling, all-success / all-failure scenarios.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.benchmark_service import (
    BenchmarkService,
    BulkBenchmarkResult,
    ShipmentBenchmarkResult,
)
from app.services.tariff_engine_service import BenchmarkResult, CostBreakdownItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_benchmark_result(expected: float = 100.0) -> BenchmarkResult:
    return BenchmarkResult(
        expected_base_amount=Decimal(str(expected)),
        expected_total_amount=Decimal(str(expected)),
        cost_breakdown=[],
        calculation_metadata={},
        classification="im_markt",
    )


def _make_shipment(shipment_id=None, tenant_id=None):
    s = MagicMock()
    s.id = shipment_id or uuid4()
    s.tenant_id = tenant_id or uuid4()
    return s


class TestBenchmarkService:
    """Test suite for BenchmarkService."""

    def setup_method(self) -> None:
        self.tariff_engine = AsyncMock()
        self.service = BenchmarkService(tariff_engine=self.tariff_engine)
        self.db = AsyncMock()
        self.tenant_id = uuid4()

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    # ============================================================================
    # calculate_benchmarks_bulk — basic cases
    # ============================================================================

    def test_empty_input_returns_zero_totals(self) -> None:
        result = self._run(
            self.service.calculate_benchmarks_bulk(self.db, [], self.tenant_id)
        )
        assert result.total == 0
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.results == []

    def test_all_succeed_returns_full_success(self) -> None:
        sid1, sid2 = uuid4(), uuid4()
        s1, s2 = _make_shipment(sid1, self.tenant_id), _make_shipment(sid2, self.tenant_id)

        # Mock DB load
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = [s1, s2]
        self.db.execute = AsyncMock(return_value=db_result)

        bench = _make_benchmark_result()
        self.tariff_engine.calculate_expected_cost = AsyncMock(return_value=bench)

        result = self._run(
            self.service.calculate_benchmarks_bulk(self.db, [sid1, sid2], self.tenant_id)
        )

        assert result.total == 2
        assert result.succeeded == 2
        assert result.failed == 0
        assert all(r.success for r in result.results)

    def test_tariff_engine_exception_isolates_to_shipment(self) -> None:
        sid1, sid2 = uuid4(), uuid4()
        s1, s2 = _make_shipment(sid1, self.tenant_id), _make_shipment(sid2, self.tenant_id)

        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = [s1, s2]
        self.db.execute = AsyncMock(return_value=db_result)

        bench = _make_benchmark_result()

        async def side_effect(db, shipment):
            if shipment.id == sid1:
                raise ValueError("No tariff found")
            return bench

        self.tariff_engine.calculate_expected_cost = side_effect

        result = self._run(
            self.service.calculate_benchmarks_bulk(self.db, [sid1, sid2], self.tenant_id)
        )

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1

        failed = next(r for r in result.results if not r.success)
        assert failed.shipment_id == sid1
        assert "No tariff found" in failed.error

        succeeded = next(r for r in result.results if r.success)
        assert succeeded.shipment_id == sid2

    # ============================================================================
    # Missing shipments
    # ============================================================================

    def test_missing_shipment_id_yields_error_result(self) -> None:
        sid = uuid4()
        # DB returns empty (RLS filtered or just not found)
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        self.db.execute = AsyncMock(return_value=db_result)

        result = self._run(
            self.service.calculate_benchmarks_bulk(self.db, [sid], self.tenant_id)
        )

        assert result.total == 1
        assert result.failed == 1
        assert result.results[0].success is False
        assert "not found" in result.results[0].error

    # ============================================================================
    # Progress callback
    # ============================================================================

    def test_progress_callback_called_for_each_shipment(self) -> None:
        sids = [uuid4(), uuid4(), uuid4()]
        shipments = [_make_shipment(sid, self.tenant_id) for sid in sids]

        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = shipments
        self.db.execute = AsyncMock(return_value=db_result)

        bench = _make_benchmark_result()
        self.tariff_engine.calculate_expected_cost = AsyncMock(return_value=bench)

        calls: list[tuple[int, int]] = []

        async def on_progress(completed: int, total: int) -> None:
            calls.append((completed, total))

        self._run(
            self.service.calculate_benchmarks_bulk(
                self.db, sids, self.tenant_id, progress_callback=on_progress
            )
        )

        assert len(calls) == 3
        totals = {c[1] for c in calls}
        assert totals == {3}
        completed_vals = sorted(c[0] for c in calls)
        assert completed_vals == [1, 2, 3]

    # ============================================================================
    # to_dict
    # ============================================================================

    def test_bulk_result_to_dict_structure(self) -> None:
        sid = uuid4()
        bench = _make_benchmark_result(50.0)
        item = ShipmentBenchmarkResult(shipment_id=sid, success=True, result=bench)
        bulk = BulkBenchmarkResult(total=1, succeeded=1, failed=0, results=[item])

        d = bulk.to_dict()
        assert d["total"] == 1
        assert d["succeeded"] == 1
        assert d["failed"] == 0
        assert len(d["results"]) == 1
        assert d["results"][0]["success"] is True
        assert d["results"][0]["shipment_id"] == str(sid)

    def test_shipment_result_to_dict_error(self) -> None:
        sid = uuid4()
        item = ShipmentBenchmarkResult(
            shipment_id=sid, success=False, error="tariff not found"
        )
        d = item.to_dict()
        assert d["success"] is False
        assert d["error"] == "tariff not found"
        assert d["result"] is None
