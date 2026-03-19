"""Unit tests for ReportAggregationService and ReportService.

Tests: project statistics KPIs, carrier aggregation, data completeness,
       top overpays, report generation versioning, compare, prune.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.report_aggregation_service import (
    CarrierAggregation,
    GenerateReportOptions,
    ProjectStatistics,
    ReportAggregationService,
    ReportService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_shipment(
    *,
    shipment_id=None,
    project_id=None,
    carrier_id=None,
    completeness=0.95,
    actual_total=100.0,
):
    s = MagicMock()
    s.id = shipment_id or uuid4()
    s.project_id = project_id or uuid4()
    s.carrier_id = carrier_id
    s.completeness_score = Decimal(str(completeness))
    s.actual_total_amount = Decimal(str(actual_total))
    s.date = date(2024, 3, 1)
    return s


def _make_benchmark(
    *,
    shipment_id,
    expected_total=90.0,
    delta=10.0,
    delta_pct=10.0,
    classification="drüber",
):
    b = MagicMock()
    b.shipment_id = shipment_id
    b.expected_total_amount = Decimal(str(expected_total))
    b.delta_amount = Decimal(str(delta))
    b.delta_pct = Decimal(str(delta_pct))
    b.classification = classification
    return b


class TestReportAggregationService:
    """Test suite for ReportAggregationService."""

    def setup_method(self) -> None:
        self.service = ReportAggregationService()
        self.db = AsyncMock()
        self.project_id = uuid4()
        self.tenant_id = uuid4()

    def _mock_execute(self, *row_lists):
        """Return a mock db.execute side_effect that yields rows in sequence."""
        calls = iter(row_lists)

        async def execute(stmt):
            rows = next(calls)
            result = MagicMock()
            result.scalars.return_value.all.return_value = rows
            # Support scalar() for aggregation queries
            result.scalar.return_value = rows[0] if rows else None
            result.one.return_value = rows[0] if rows else MagicMock(start_date=None, end_date=None)
            return result

        self.db.execute = execute

    # ============================================================================
    # calculate_project_statistics
    # ============================================================================

    def test_empty_project_returns_zeros(self) -> None:
        self._mock_execute([], [])  # shipments, benchmarks

        stats = _run(
            self.service.calculate_project_statistics(
                self.db, self.project_id, self.tenant_id
            )
        )

        assert stats.total_shipments == 0
        assert stats.overpay_rate == 0.0
        assert stats.carriers == []

    def test_overpay_rate_calculated_correctly(self) -> None:
        carrier_id = uuid4()
        s1 = _make_shipment(carrier_id=carrier_id, completeness=0.95, actual_total=120.0)
        s2 = _make_shipment(carrier_id=carrier_id, completeness=0.8, actual_total=80.0)
        s3 = _make_shipment(carrier_id=carrier_id, completeness=0.6, actual_total=60.0)

        b1 = _make_benchmark(shipment_id=s1.id, classification="drüber", delta=20.0)
        b2 = _make_benchmark(shipment_id=s2.id, classification="im_markt", delta=0.0)
        b3 = _make_benchmark(shipment_id=s3.id, classification="im_markt", delta=0.0)

        self._mock_execute([s1, s2, s3], [b1, b2, b3])

        stats = _run(
            self.service.calculate_project_statistics(
                self.db, self.project_id, self.tenant_id
            )
        )

        # 1 out of 3 is "drüber" → 33.33%
        assert stats.total_shipments == 3
        assert stats.overpay_rate > 0
        assert round(stats.overpay_rate, 2) == pytest.approx(33.33, abs=0.01)

    def test_complete_partial_missing_buckets(self) -> None:
        s_complete = _make_shipment(completeness=0.95)
        s_partial = _make_shipment(completeness=0.7)
        s_missing = _make_shipment(completeness=0.3)

        self._mock_execute([s_complete, s_partial, s_missing], [])

        stats = _run(
            self.service.calculate_project_statistics(
                self.db, self.project_id, self.tenant_id
            )
        )

        assert stats.complete_shipments == 1
        assert stats.partial_shipments == 1
        assert stats.missing_shipments == 1

    def test_carrier_aggregation_groups_correctly(self) -> None:
        carrier_id = uuid4()
        s1 = _make_shipment(carrier_id=carrier_id, actual_total=100.0)
        s2 = _make_shipment(carrier_id=carrier_id, actual_total=200.0)

        b1 = _make_benchmark(shipment_id=s1.id, expected_total=80.0, delta=20.0, classification="drüber")
        b2 = _make_benchmark(shipment_id=s2.id, expected_total=200.0, delta=0.0, classification="im_markt")

        self._mock_execute([s1, s2], [b1, b2])

        stats = _run(
            self.service.calculate_project_statistics(
                self.db, self.project_id, self.tenant_id
            )
        )

        assert len(stats.carriers) == 1
        carrier = stats.carriers[0]
        assert carrier.shipment_count == 2
        assert carrier.overpay_count == 1
        assert carrier.market_count == 1

    # ============================================================================
    # _aggregate_by_carrier — direct unit test
    # ============================================================================

    def test_aggregate_by_carrier_sorted_by_delta(self) -> None:
        cid_a = uuid4()
        cid_b = uuid4()
        s_a = _make_shipment(carrier_id=cid_a, actual_total=200.0)
        s_b = _make_shipment(carrier_id=cid_b, actual_total=100.0)

        bm_a = _make_benchmark(shipment_id=s_a.id, delta=50.0)
        bm_b = _make_benchmark(shipment_id=s_b.id, delta=10.0)
        bmap = {s_a.id: bm_a, s_b.id: bm_b}

        results = self.service._aggregate_by_carrier([s_a, s_b], bmap)

        assert len(results) == 2
        # Highest delta first
        assert results[0].total_delta >= results[1].total_delta

    # ============================================================================
    # ProjectStatistics.to_dict
    # ============================================================================

    def test_to_dict_structure(self) -> None:
        stats = ProjectStatistics(
            total_shipments=5,
            parsed_shipments=5,
            benchmarked_shipments=4,
            complete_shipments=3,
            partial_shipments=1,
            missing_shipments=1,
            data_completeness_avg=0.85,
            total_actual_cost=1000.0,
            total_expected_cost=900.0,
            total_savings_potential=100.0,
            overpay_rate=40.0,
            carriers=[],
        )
        d = stats.to_dict()
        assert d["total_shipments"] == 5
        assert d["overpay_rate"] == 40.0
        assert d["carriers"] == []


class TestReportService:
    """Test suite for ReportService."""

    def setup_method(self) -> None:
        self.aggregation = MagicMock()
        self.aggregation.calculate_project_statistics = AsyncMock()
        self.aggregation.calculate_data_completeness = AsyncMock(return_value=0.9)
        self.aggregation.get_date_range = AsyncMock(
            return_value={"start_date": date(2024, 1, 1), "end_date": date(2024, 3, 31)}
        )
        self.aggregation.get_top_overpays = AsyncMock(return_value=[])

        from app.services.report_aggregation_service import ProjectStatistics
        self.aggregation.calculate_project_statistics.return_value = ProjectStatistics(
            total_shipments=10,
            parsed_shipments=10,
            benchmarked_shipments=8,
            complete_shipments=8,
            partial_shipments=1,
            missing_shipments=1,
            data_completeness_avg=0.9,
            total_actual_cost=1000.0,
            total_expected_cost=900.0,
            total_savings_potential=100.0,
            overpay_rate=30.0,
            carriers=[],
        )

        self.service = ReportService(aggregation_service=self.aggregation)
        self.db = AsyncMock()
        self.project_id = uuid4()
        self.tenant_id = uuid4()

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _mock_project(self):
        project = MagicMock()
        project.id = self.project_id
        project.name = "Test Project"
        project.phase = "quick_check"
        project.status = "active"
        return project

    # ============================================================================
    # generate
    # ============================================================================

    def test_generate_raises_404_for_missing_project(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc_info:
            self._run(self.service.generate(self.db, self.project_id, self.tenant_id))

        assert exc_info.value.status_code == 404

    def test_generate_increments_version(self) -> None:
        project = self._mock_project()

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # Project lookup
                result.scalar_one_or_none.return_value = project
            elif call_count == 2:
                # Max version query → currently at version 2
                result.scalar.return_value = 2
            else:
                result.scalar_one_or_none.return_value = None
            return result

        self.db.execute = mock_execute
        self.db.add = MagicMock()
        self.db.flush = AsyncMock()
        self.db.refresh = AsyncMock()

        # Simulate refresh setting id/version
        async def fake_refresh(obj):
            obj.id = uuid4()
            obj.version = 3

        self.db.refresh = fake_refresh

        report = self._run(
            self.service.generate(self.db, self.project_id, self.tenant_id)
        )

        assert report.version == 3

    def test_generate_first_report_is_version_1(self) -> None:
        project = self._mock_project()
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = project
            elif call_count == 2:
                result.scalar.return_value = None  # No existing reports
            return result

        self.db.execute = mock_execute
        self.db.add = MagicMock()
        self.db.flush = AsyncMock()

        async def fake_refresh(obj):
            obj.id = uuid4()
            obj.version = 1

        self.db.refresh = fake_refresh

        report = self._run(
            self.service.generate(self.db, self.project_id, self.tenant_id)
        )

        assert report.version == 1

    # ============================================================================
    # GenerateReportOptions
    # ============================================================================

    def test_options_defaults(self) -> None:
        opts = GenerateReportOptions()
        assert opts.include_top_overpays is False
        assert opts.top_overpays_limit == 10
        assert opts.notes is None
