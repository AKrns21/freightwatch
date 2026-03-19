"""Report Aggregation Service — project-level KPI calculation and report lifecycle.

Port of backend_legacy/src/modules/report/report-aggregation.service.ts
     + backend_legacy/src/modules/report/report.service.ts
Issue: #48

Key features:
- calculateProjectStatistics(): total_shipments, overpay_rate, carrier breakdowns
- calculateDataCompleteness(): avg completeness_score across shipments
- getTopOverpays(): top-N drüber-classified benchmarks
- getDateRange(): MIN/MAX shipment.date for project
- ReportService.generate(): creates versioned Report snapshot
- ReportService.get_latest() / get_by_version() / list_all() / compare()
- ReportService.prune_old_versions(): keeps latest N, deletes the rest
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Project, Report, Shipment, ShipmentBenchmark
from app.utils.round import round_monetary

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class CarrierAggregation:
    """Aggregated statistics for a single carrier within a project."""

    carrier_id: str
    carrier_name: str
    shipment_count: int
    total_actual_cost: float
    total_expected_cost: float
    total_delta: float
    avg_delta_pct: float
    overpay_count: int
    underpay_count: int
    market_count: int
    data_completeness_avg: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "carrier_id": self.carrier_id,
            "carrier_name": self.carrier_name,
            "shipment_count": self.shipment_count,
            "total_actual_cost": self.total_actual_cost,
            "total_expected_cost": self.total_expected_cost,
            "total_delta": self.total_delta,
            "avg_delta_pct": self.avg_delta_pct,
            "overpay_count": self.overpay_count,
            "underpay_count": self.underpay_count,
            "market_count": self.market_count,
            "data_completeness_avg": self.data_completeness_avg,
        }


@dataclass
class ProjectStatistics:
    """Overall project-level statistics."""

    total_shipments: int
    parsed_shipments: int
    benchmarked_shipments: int
    complete_shipments: int
    partial_shipments: int
    missing_shipments: int
    data_completeness_avg: float
    total_actual_cost: float
    total_expected_cost: float
    total_savings_potential: float
    overpay_rate: float
    carriers: list[CarrierAggregation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_shipments": self.total_shipments,
            "parsed_shipments": self.parsed_shipments,
            "benchmarked_shipments": self.benchmarked_shipments,
            "complete_shipments": self.complete_shipments,
            "partial_shipments": self.partial_shipments,
            "missing_shipments": self.missing_shipments,
            "data_completeness_avg": self.data_completeness_avg,
            "total_actual_cost": self.total_actual_cost,
            "total_expected_cost": self.total_expected_cost,
            "total_savings_potential": self.total_savings_potential,
            "overpay_rate": self.overpay_rate,
            "carriers": [c.to_dict() for c in self.carriers],
        }


# ---------------------------------------------------------------------------
# Aggregation service
# ---------------------------------------------------------------------------


class ReportAggregationService:
    """Data aggregation logic for project reports.

    Calculates project-level and carrier-level KPIs from shipment_benchmark rows.

    Example usage:
        svc = ReportAggregationService()
        stats = await svc.calculate_project_statistics(db, project_id, tenant_id)
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    async def calculate_project_statistics(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
    ) -> ProjectStatistics:
        """Calculate project-level statistics.

        Args:
            db: Async DB session with tenant context set.
            project_id: Target project.
            tenant_id: Tenant UUID (used for RLS context confirmation in logs).

        Returns:
            ProjectStatistics dataclass.
        """
        self.logger.info(
            "calculate_project_statistics_start",
            project_id=str(project_id),
            tenant_id=str(tenant_id),
        )

        # Load all shipments for project (RLS filters by tenant automatically)
        shipment_result = await db.execute(
            select(Shipment).where(Shipment.project_id == project_id)
        )
        shipments = list(shipment_result.scalars().all())

        # Load all benchmarks for shipments in this project
        shipment_ids = [s.id for s in shipments]
        benchmark_map: dict[UUID, ShipmentBenchmark] = {}
        if shipment_ids:
            bench_result = await db.execute(
                select(ShipmentBenchmark).where(
                    ShipmentBenchmark.shipment_id.in_(shipment_ids)
                )
            )
            for b in bench_result.scalars().all():
                benchmark_map[b.shipment_id] = b

        # Aggregate overall stats
        complete_count = 0
        partial_count = 0
        missing_count = 0
        total_actual = 0.0
        total_expected = 0.0
        total_delta = 0.0
        completeness_sum = 0.0
        overpay_count = 0

        for shipment in shipments:
            completeness = float(shipment.completeness_score or 0)
            completeness_sum += completeness

            if completeness >= 0.9:
                complete_count += 1
            elif completeness >= 0.5:
                partial_count += 1
            else:
                missing_count += 1

            benchmark = benchmark_map.get(shipment.id)
            if benchmark:
                total_actual += float(shipment.actual_total_amount or 0)
                total_expected += float(benchmark.expected_total_amount or 0)
                total_delta += float(benchmark.delta_amount or 0)
                if benchmark.classification == "drüber":
                    overpay_count += 1

        n = len(shipments)
        avg_completeness = float(round_monetary(completeness_sum / n)) if n > 0 else 0.0
        overpay_rate = float(round_monetary(overpay_count / n * 100)) if n > 0 else 0.0

        # Carrier-level aggregation
        carriers = self._aggregate_by_carrier(shipments, benchmark_map)

        stats = ProjectStatistics(
            total_shipments=n,
            parsed_shipments=n,  # All DB rows are parsed
            benchmarked_shipments=len(benchmark_map),
            complete_shipments=complete_count,
            partial_shipments=partial_count,
            missing_shipments=missing_count,
            data_completeness_avg=avg_completeness,
            total_actual_cost=float(round_monetary(total_actual)),
            total_expected_cost=float(round_monetary(total_expected)),
            total_savings_potential=float(round_monetary(total_delta)),
            overpay_rate=overpay_rate,
            carriers=carriers,
        )

        self.logger.info(
            "calculate_project_statistics_complete",
            project_id=str(project_id),
            total_shipments=stats.total_shipments,
            overpay_rate=stats.overpay_rate,
        )
        return stats

    async def calculate_data_completeness(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
    ) -> float:
        """Return average completeness_score for all shipments in project.

        Returns:
            Float in [0, 1]; 0 if no shipments exist.
        """
        result = await db.execute(
            select(func.avg(Shipment.completeness_score)).where(
                Shipment.project_id == project_id
            )
        )
        avg = result.scalar()
        if avg is None:
            return 0.0
        return float(round_monetary(avg))

    async def get_top_overpays(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
        limit: int = 10,
    ) -> list[ShipmentBenchmark]:
        """Return top-N over-paid shipments (classification='drüber').

        Returns:
            List of ShipmentBenchmark rows ordered by delta_amount DESC.
        """
        # Load all drüber benchmarks for this project
        shipment_result = await db.execute(
            select(Shipment.id).where(Shipment.project_id == project_id)
        )
        shipment_ids = list(shipment_result.scalars().all())
        if not shipment_ids:
            return []

        bench_result = await db.execute(
            select(ShipmentBenchmark)
            .where(
                ShipmentBenchmark.shipment_id.in_(shipment_ids),
                ShipmentBenchmark.classification == "drüber",
            )
            .order_by(ShipmentBenchmark.delta_amount.desc())
            .limit(limit)
        )
        return list(bench_result.scalars().all())

    async def get_date_range(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, date | None]:
        """Return MIN/MAX shipment date for a project.

        Returns:
            Dict with ``start_date`` and ``end_date`` keys (may be None).
        """
        result = await db.execute(
            select(
                func.min(Shipment.date).label("start_date"),
                func.max(Shipment.date).label("end_date"),
            ).where(Shipment.project_id == project_id)
        )
        row = result.one()
        return {"start_date": row.start_date, "end_date": row.end_date}

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _aggregate_by_carrier(
        self,
        shipments: list[Shipment],
        benchmark_map: dict[UUID, ShipmentBenchmark],
    ) -> list[CarrierAggregation]:
        """Group shipments by carrier_id and compute per-carrier KPIs."""
        groups: dict[str, dict[str, Any]] = {}

        for shipment in shipments:
            if not shipment.carrier_id:
                continue
            cid = str(shipment.carrier_id)
            if cid not in groups:
                groups[cid] = {
                    "name": "Unknown",
                    "shipments": [],
                }
            groups[cid]["shipments"].append(shipment)

        results: list[CarrierAggregation] = []
        for carrier_id, data in groups.items():
            total_actual = 0.0
            total_expected = 0.0
            total_delta = 0.0
            delta_pct_sum = 0.0
            overpay_count = 0
            underpay_count = 0
            market_count = 0
            completeness_sum = 0.0
            benchmarked_count = 0

            for shipment in data["shipments"]:
                benchmark = benchmark_map.get(shipment.id)
                if benchmark:
                    total_actual += float(shipment.actual_total_amount or 0)
                    total_expected += float(benchmark.expected_total_amount or 0)
                    total_delta += float(benchmark.delta_amount or 0)
                    delta_pct_sum += float(benchmark.delta_pct or 0)
                    benchmarked_count += 1

                    cls = benchmark.classification
                    if cls == "drüber":
                        overpay_count += 1
                    elif cls == "unter":
                        underpay_count += 1
                    elif cls == "im_markt":
                        market_count += 1

                completeness_sum += float(shipment.completeness_score or 0)

            n = len(data["shipments"])
            avg_delta_pct = (
                float(round_monetary(delta_pct_sum / benchmarked_count))
                if benchmarked_count > 0
                else 0.0
            )
            avg_completeness = float(round_monetary(completeness_sum / n)) if n > 0 else 0.0

            results.append(
                CarrierAggregation(
                    carrier_id=carrier_id,
                    carrier_name=data["name"],
                    shipment_count=n,
                    total_actual_cost=float(round_monetary(total_actual)),
                    total_expected_cost=float(round_monetary(total_expected)),
                    total_delta=float(round_monetary(total_delta)),
                    avg_delta_pct=avg_delta_pct,
                    overpay_count=overpay_count,
                    underpay_count=underpay_count,
                    market_count=market_count,
                    data_completeness_avg=avg_completeness,
                )
            )

        # Sort by total_delta descending (highest overpay first)
        results.sort(key=lambda x: x.total_delta, reverse=True)
        return results


# ---------------------------------------------------------------------------
# Report Service
# ---------------------------------------------------------------------------


@dataclass
class GenerateReportOptions:
    """Options for report generation."""

    include_top_overpays: bool = False
    top_overpays_limit: int = 10
    notes: str | None = None


class ReportService:
    """Report lifecycle management — generation, versioning, and pruning.

    Creates immutable data snapshots per project; each call to generate()
    increments the version number. Delegates aggregation to
    ReportAggregationService.

    Example usage:
        svc = ReportService()
        report = await svc.generate(db, project_id, tenant_id)
    """

    def __init__(self, aggregation_service: ReportAggregationService | None = None) -> None:
        self.aggregation = aggregation_service or ReportAggregationService()
        self.logger = structlog.get_logger(__name__)

    async def generate(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
        options: GenerateReportOptions | None = None,
    ) -> Report:
        """Generate a new versioned report for a project.

        Args:
            db: Async DB session with tenant context set.
            project_id: Project to generate the report for.
            tenant_id: Current tenant (stored as generated_by).
            options: Optional generation parameters.

        Returns:
            Persisted Report ORM instance.

        Raises:
            HTTPException(404): Project not found.
        """
        if options is None:
            options = GenerateReportOptions()

        self.logger.info("generate_report_start", project_id=str(project_id))

        # Verify project exists (RLS ensures tenant isolation)
        project_result = await db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = project_result.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

        # Determine next version
        version_result = await db.execute(
            select(func.max(Report.version)).where(Report.project_id == project_id)
        )
        max_version = version_result.scalar() or 0
        next_version = max_version + 1

        # Aggregate
        statistics = await self.aggregation.calculate_project_statistics(
            db, project_id, tenant_id
        )
        data_completeness = await self.aggregation.calculate_data_completeness(
            db, project_id, tenant_id
        )
        date_range = await self.aggregation.get_date_range(db, project_id, tenant_id)

        snapshot: dict[str, Any] = {
            "version": next_version,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "project": {
                "id": str(project.id),
                "name": project.name,
                "phase": project.phase,
                "status": project.status,
            },
            "statistics": statistics.to_dict(),
            "data_completeness": data_completeness,
        }

        if options.notes:
            snapshot["notes"] = options.notes

        if options.include_top_overpays:
            top_overpays = await self.aggregation.get_top_overpays(
                db, project_id, tenant_id, options.top_overpays_limit
            )
            # Load matching shipment data for each benchmark
            shipment_ids = [b.shipment_id for b in top_overpays]
            shipment_map: dict[UUID, Shipment] = {}
            if shipment_ids:
                s_result = await db.execute(
                    select(Shipment).where(Shipment.id.in_(shipment_ids))
                )
                for s in s_result.scalars().all():
                    shipment_map[s.id] = s

            snapshot["top_overpays"] = [
                {
                    "shipment_id": str(b.shipment_id),
                    "date": str(shipment_map[b.shipment_id].date)
                    if b.shipment_id in shipment_map
                    else None,
                    "origin_zip": shipment_map[b.shipment_id].origin_zip
                    if b.shipment_id in shipment_map
                    else None,
                    "dest_zip": shipment_map[b.shipment_id].dest_zip
                    if b.shipment_id in shipment_map
                    else None,
                    "actual_cost": float(b.actual_total_amount or 0),
                    "expected_cost": float(b.expected_total_amount or 0),
                    "delta": float(b.delta_amount or 0),
                    "delta_pct": float(b.delta_pct or 0),
                }
                for b in top_overpays
            ]

        # Persist report
        report = Report(
            project_id=project_id,
            version=next_version,
            report_type=project.phase or "quick_check",
            title=f"{project.phase or 'quick_check'} Report v{next_version}",
            data_snapshot=snapshot,
            shipment_count=statistics.total_shipments,
            date_range_start=date_range.get("start_date"),
            date_range_end=date_range.get("end_date"),
            generated_by=tenant_id,
        )
        db.add(report)
        await db.flush()
        await db.refresh(report)

        self.logger.info(
            "generate_report_complete",
            project_id=str(project_id),
            report_id=str(report.id),
            version=next_version,
            data_completeness=data_completeness,
        )
        return report

    async def get_latest(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
    ) -> Report | None:
        """Return the latest report version for a project, or None."""
        await self._assert_project_exists(db, project_id)
        result = await db.execute(
            select(Report)
            .where(Report.project_id == project_id)
            .order_by(Report.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_version(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
        version: int,
    ) -> Report | None:
        """Return a specific report version, or None."""
        await self._assert_project_exists(db, project_id)
        result = await db.execute(
            select(Report).where(
                Report.project_id == project_id,
                Report.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
    ) -> list[Report]:
        """Return all report versions for a project, newest first."""
        await self._assert_project_exists(db, project_id)
        result = await db.execute(
            select(Report)
            .where(Report.project_id == project_id)
            .order_by(Report.version.desc())
        )
        return list(result.scalars().all())

    async def compare(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
        version1: int,
        version2: int,
    ) -> dict[str, Any]:
        """Compare two report versions and return a delta summary.

        Returns:
            Dict with ``report1``, ``report2``, and ``delta`` keys.

        Raises:
            HTTPException(404): One or both versions not found.
        """
        r1 = await self.get_by_version(db, project_id, tenant_id, version1)
        r2 = await self.get_by_version(db, project_id, tenant_id, version2)

        if r1 is None or r2 is None:
            raise HTTPException(
                status_code=404,
                detail="One or both report versions not found",
            )

        stats1: dict[str, Any] = r1.data_snapshot.get("statistics", {})
        stats2: dict[str, Any] = r2.data_snapshot.get("statistics", {})
        completeness1 = float(r1.data_snapshot.get("data_completeness", 0))
        completeness2 = float(r2.data_snapshot.get("data_completeness", 0))

        return {
            "report1": r1,
            "report2": r2,
            "delta": {
                "shipments": stats2.get("total_shipments", 0) - stats1.get("total_shipments", 0),
                "completeness": completeness2 - completeness1,
                "savings_potential": (
                    stats2.get("total_savings_potential", 0.0)
                    - stats1.get("total_savings_potential", 0.0)
                ),
            },
        }

    async def prune_old_versions(
        self,
        db: AsyncSession,
        project_id: UUID,
        tenant_id: UUID,
        keep_versions: int = 5,
    ) -> int:
        """Delete old report versions, keeping the latest ``keep_versions``.

        Returns:
            Number of deleted report rows.
        """
        await self._assert_project_exists(db, project_id)

        result = await db.execute(
            select(Report)
            .where(Report.project_id == project_id)
            .order_by(Report.version.desc())
        )
        reports = list(result.scalars().all())

        to_delete = reports[keep_versions:]
        if not to_delete:
            return 0

        for report in to_delete:
            await db.delete(report)

        self.logger.info(
            "pruned_old_reports",
            project_id=str(project_id),
            deleted_count=len(to_delete),
        )
        return len(to_delete)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _assert_project_exists(
        self,
        db: AsyncSession,
        project_id: UUID,
    ) -> None:
        result = await db.execute(
            select(Project.id).where(Project.id == project_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_aggregation_service: ReportAggregationService | None = None
_report_service: ReportService | None = None


def get_aggregation_service() -> ReportAggregationService:
    global _aggregation_service
    if _aggregation_service is None:
        _aggregation_service = ReportAggregationService()
    return _aggregation_service


def get_report_service() -> ReportService:
    global _report_service
    if _report_service is None:
        _report_service = ReportService()
    return _report_service
