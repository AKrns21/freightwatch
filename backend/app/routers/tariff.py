"""Tariff router — read access to extracted tariff tables.

GET /api/tariffs/{tariff_table_id}  → header + rates + zone_maps
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.tenant_middleware import get_current_tenant_db
from app.models.database import TariffRate, TariffTable, TariffZoneMap

router = APIRouter(prefix="/tariffs", tags=["tariffs"])


class TariffRateOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    zone: int
    weight_from_kg: Decimal
    weight_to_kg: Decimal
    rate_per_shipment: Decimal | None = None
    rate_per_kg: Decimal | None = None


class TariffZoneMapOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    country_code: str
    plz_prefix: str
    match_type: str
    zone: int


class TariffDetailResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    name: str | None = None
    carrier_id: UUID
    upload_id: UUID | None = None
    lane_type: str
    currency: str
    valid_from: str  # ISO date string
    valid_until: str | None = None
    confidence: Decimal | None = None
    rates: list[TariffRateOut]
    zone_maps: list[TariffZoneMapOut]


@router.get("/{tariff_table_id}", response_model=TariffDetailResponse)
async def get_tariff(
    tariff_table_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> TariffDetailResponse:
    """Return tariff header, rate matrix, and zone map for one tariff table."""
    tariff = (
        await db.execute(select(TariffTable).where(TariffTable.id == tariff_table_id))
    ).scalar_one_or_none()

    if tariff is None:
        raise HTTPException(status_code=404, detail="Tariff table not found")

    rates = (
        await db.execute(
            select(TariffRate)
            .where(TariffRate.tariff_table_id == tariff_table_id)
            .order_by(TariffRate.zone, TariffRate.weight_from_kg)
        )
    ).scalars().all()

    zone_maps = (
        await db.execute(
            select(TariffZoneMap)
            .where(TariffZoneMap.tariff_table_id == tariff_table_id)
            .order_by(TariffZoneMap.plz_prefix)
        )
    ).scalars().all()

    return TariffDetailResponse(
        id=tariff.id,
        name=tariff.name,
        carrier_id=tariff.carrier_id,
        upload_id=tariff.upload_id,
        lane_type=tariff.lane_type,
        currency=tariff.currency,
        valid_from=tariff.valid_from.isoformat(),
        valid_until=tariff.valid_until.isoformat() if tariff.valid_until else None,
        confidence=tariff.confidence,
        rates=[TariffRateOut.model_validate(r) for r in rates],
        zone_maps=[TariffZoneMapOut.model_validate(z) for z in zone_maps],
    )
