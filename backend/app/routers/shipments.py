"""Shipments router — paginated shipment listing per project.

GET /api/projects/{id}/shipments  → paginated shipment list with filters

Port of backend_legacy/src/modules/parsing/entities/shipment.entity.ts
Issue: #50
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.tenant_middleware import get_current_tenant_db
from app.models.database import Shipment, ShipmentBenchmark

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["shipments"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BenchmarkSummary(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    expected_total_amount: Decimal | None = None
    actual_total_amount: Decimal | None = None
    delta_amount: Decimal | None = None
    delta_pct: Decimal | None = None
    classification: str | None = None


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    project_id: UUID | None = None
    upload_id: UUID | None = None
    carrier_id: UUID | None = None
    date: date
    reference_number: str | None = None
    service_level: str | None = None
    origin_zip: str | None = None
    origin_country: str | None = None
    dest_zip: str | None = None
    dest_country: str | None = None
    weight_kg: Decimal | None = None
    volume_cbm: Decimal | None = None
    pallets: Decimal | None = None
    chargeable_weight_kg: Decimal | None = None
    chargeable_basis: str | None = None
    currency: str | None = None
    actual_total_amount: Decimal | None = None
    actual_base_amount: Decimal | None = None
    actual_diesel_amount: Decimal | None = None
    actual_toll_amount: Decimal | None = None
    completeness_score: Decimal | None = None
    created_at: datetime | None = None
    benchmark: BenchmarkSummary | None = None


class ShipmentPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[ShipmentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{project_id}/shipments", response_model=ShipmentPage)
async def list_shipments(
    project_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    classification: str | None = Query(None, description="Filter by benchmark classification"),
    carrier_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_current_tenant_db),
) -> ShipmentPage:
    """List shipments for a project with pagination and optional filters."""
    base_q = select(Shipment).where(
        Shipment.project_id == project_id,
        Shipment.deleted_at.is_(None),
    )

    if carrier_id is not None:
        base_q = base_q.where(Shipment.carrier_id == carrier_id)

    # Apply classification filter via join if needed
    if classification is not None:
        base_q = base_q.join(
            ShipmentBenchmark,
            ShipmentBenchmark.shipment_id == Shipment.id,
            isouter=False,
        ).where(ShipmentBenchmark.classification == classification)

    # Count total
    from sqlalchemy import func

    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    # Paginated rows
    offset = (page - 1) * page_size
    rows = (
        await db.execute(
            base_q.order_by(Shipment.date.desc(), Shipment.id).offset(offset).limit(page_size)
        )
    ).scalars().all()

    # Fetch benchmarks for these shipments in one query
    ship_ids = [s.id for s in rows]
    benchmarks: dict[UUID, ShipmentBenchmark] = {}
    if ship_ids:
        bm_rows = (
            await db.execute(
                select(ShipmentBenchmark).where(ShipmentBenchmark.shipment_id.in_(ship_ids))
            )
        ).scalars().all()
        for bm_row in bm_rows:
            benchmarks[bm_row.shipment_id] = bm_row

    items = []
    for s in rows:
        bm = benchmarks.get(s.id)
        items.append(
            ShipmentResponse(
                id=s.id,
                project_id=s.project_id,
                upload_id=s.upload_id,
                carrier_id=s.carrier_id,
                date=s.date,
                reference_number=s.reference_number,
                service_level=s.service_level,
                origin_zip=s.origin_zip,
                origin_country=s.origin_country,
                dest_zip=s.dest_zip,
                dest_country=s.dest_country,
                weight_kg=s.weight_kg,
                volume_cbm=s.volume_cbm,
                pallets=s.pallets,
                chargeable_weight_kg=s.chargeable_weight_kg,
                chargeable_basis=s.chargeable_basis,
                currency=s.currency,
                actual_total_amount=s.actual_total_amount,
                actual_base_amount=s.actual_base_amount,
                actual_diesel_amount=s.actual_diesel_amount,
                actual_toll_amount=s.actual_toll_amount,
                completeness_score=s.completeness_score,
                created_at=s.created_at,
                benchmark=BenchmarkSummary.model_validate(bm) if bm else None,
            )
        )

    total_pages = max(1, (total + page_size - 1) // page_size)
    return ShipmentPage(
        items=items, total=total, page=page, page_size=page_size, total_pages=total_pages
    )
