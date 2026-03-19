"""Benchmark Service — bulk expected-cost calculation for shipments.

Port of backend_legacy/src/modules/tariff/ (bulk processing logic)
Issue: #48

Key features:
- calculate_benchmarks_bulk(): asyncio.Semaphore(5) concurrent processing
- Calls TariffEngineService.calculate_expected_cost() per shipment
- Progress callback for status tracking
- Returns per-shipment results with error isolation
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Shipment
from app.services.tariff_engine_service import BenchmarkResult, TariffEngineService

logger = structlog.get_logger(__name__)

_BULK_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ShipmentBenchmarkResult:
    """Per-shipment result from bulk processing."""

    shipment_id: UUID
    success: bool
    result: BenchmarkResult | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "shipment_id": str(self.shipment_id),
            "success": self.success,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
        }


@dataclass
class BulkBenchmarkResult:
    """Aggregated result of a bulk benchmark run."""

    total: int
    succeeded: int
    failed: int
    results: list[ShipmentBenchmarkResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# ProgressCallback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int], Awaitable[None]]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class BenchmarkService:
    """Bulk benchmark orchestrator.

    Loads shipments by ID, then fans out to TariffEngineService with a
    semaphore-bounded concurrency of 5. Errors per shipment are caught and
    recorded without aborting the run.

    Example usage:
        svc = BenchmarkService()
        result = await svc.calculate_benchmarks_bulk(db, shipment_ids, tenant_id)
    """

    def __init__(self, tariff_engine: TariffEngineService | None = None) -> None:
        self.tariff_engine = tariff_engine or TariffEngineService()
        self.logger = structlog.get_logger(__name__)

    async def calculate_benchmarks_bulk(
        self,
        db: AsyncSession,
        shipment_ids: list[UUID],
        tenant_id: UUID,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> BulkBenchmarkResult:
        """Calculate expected costs for multiple shipments concurrently.

        Args:
            db: Async DB session with tenant context already set via RLS.
            shipment_ids: List of shipment IDs to process.
            tenant_id: Tenant UUID (for logging / validation).
            progress_callback: Optional async callback invoked after each
                shipment finishes: ``await callback(completed_count, total)``.

        Returns:
            BulkBenchmarkResult summarising successes and failures.
        """
        total = len(shipment_ids)
        self.logger.info(
            "bulk_benchmark_started",
            tenant_id=str(tenant_id),
            total=total,
            concurrency=_BULK_CONCURRENCY,
        )

        # Load all shipments in one query
        shipments = await self._load_shipments(db, shipment_ids, tenant_id)
        shipment_map: dict[UUID, Shipment] = {s.id: s for s in shipments}

        semaphore = asyncio.Semaphore(_BULK_CONCURRENCY)
        completed = 0
        results: list[ShipmentBenchmarkResult] = []

        async def _process_one(shipment_id: UUID) -> ShipmentBenchmarkResult:
            nonlocal completed
            async with semaphore:
                shipment = shipment_map.get(shipment_id)
                if shipment is None:
                    res = ShipmentBenchmarkResult(
                        shipment_id=shipment_id,
                        success=False,
                        error=f"Shipment {shipment_id} not found or not accessible",
                    )
                else:
                    res = await self._calculate_one(db, shipment)

                completed += 1
                if progress_callback is not None:
                    await progress_callback(completed, total)
                return res

        tasks = [_process_one(sid) for sid in shipment_ids]
        results = list(await asyncio.gather(*tasks))

        succeeded = sum(1 for r in results if r.success)
        failed = total - succeeded

        self.logger.info(
            "bulk_benchmark_completed",
            tenant_id=str(tenant_id),
            total=total,
            succeeded=succeeded,
            failed=failed,
        )

        return BulkBenchmarkResult(
            total=total,
            succeeded=succeeded,
            failed=failed,
            results=results,
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _load_shipments(
        self,
        db: AsyncSession,
        shipment_ids: list[UUID],
        tenant_id: UUID,
    ) -> list[Shipment]:
        """Load shipments from DB (RLS ensures tenant isolation)."""
        if not shipment_ids:
            return []
        stmt = select(Shipment).where(Shipment.id.in_(shipment_ids))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def _calculate_one(
        self,
        db: AsyncSession,
        shipment: Shipment,
    ) -> ShipmentBenchmarkResult:
        """Run tariff engine for a single shipment; isolate errors."""
        try:
            result = await self.tariff_engine.calculate_expected_cost(db, shipment)
            self.logger.debug(
                "shipment_benchmark_ok",
                shipment_id=str(shipment.id),
                expected=str(result.expected_total_amount),
                classification=result.classification,
            )
            return ShipmentBenchmarkResult(
                shipment_id=shipment.id,
                success=True,
                result=result,
            )
        except Exception as exc:
            self.logger.warning(
                "shipment_benchmark_failed",
                shipment_id=str(shipment.id),
                error=str(exc),
            )
            return ShipmentBenchmarkResult(
                shipment_id=shipment.id,
                success=False,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_benchmark_service: BenchmarkService | None = None


def get_benchmark_service() -> BenchmarkService:
    global _benchmark_service
    if _benchmark_service is None:
        _benchmark_service = BenchmarkService()
    return _benchmark_service
