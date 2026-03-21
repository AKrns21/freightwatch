"""Carriers router — global carrier listing and per-tenant alias management.

GET    /api/carriers                                  → list all carriers
GET    /api/carriers/{id}/aliases                     → list tenant's aliases for a carrier
POST   /api/carriers/{id}/aliases                     → create alias mapping
DELETE /api/carriers/{id}/aliases/{alias_text}        → delete an alias

Port of backend_legacy/src/modules/upload/entities/carrier-alias.entity.ts
Issue: #50
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.tenant_middleware import get_current_tenant_db
from app.models.database import Carrier, CarrierAlias

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/carriers", tags=["carriers"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CarrierResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    name: str
    code_norm: str
    country: str | None = None
    created_at: datetime | None = None


class AliasResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    alias_text: str
    carrier_id: UUID


class CreateCarrierRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str = Field(..., max_length=255)
    code_norm: str = Field(..., max_length=50)
    country: str | None = Field(None, max_length=2)
    # First alias to register for this tenant (defaults to name.lower())
    alias_text: str | None = Field(None, max_length=255)


class CreateAliasRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    alias_text: str = Field(..., max_length=255)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=CarrierResponse, status_code=201)
async def create_carrier(
    body: CreateCarrierRequest,
    request: Request,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> CarrierResponse:
    """Create a new global carrier and register the first tenant-scoped alias."""
    existing = (
        await db.execute(select(Carrier).where(Carrier.code_norm == body.code_norm))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Carrier code '{body.code_norm}' already exists")

    carrier = Carrier(
        name=body.name.strip(),
        code_norm=body.code_norm.strip(),
        country=body.country,
        conversion_rules={},
        billing_type_map={},
    )
    db.add(carrier)
    await db.flush()  # populate carrier.id

    alias_text = (body.alias_text or body.name).strip().lower()
    tenant_id = getattr(request.state, "tenant_id", None)
    await db.execute(
        pg_insert(CarrierAlias)
        .values(tenant_id=tenant_id, alias_text=alias_text, carrier_id=carrier.id)
        .on_conflict_do_update(
            index_elements=["tenant_id", "alias_text"],
            set_={"carrier_id": carrier.id},
        )
    )

    logger.info("carrier_created", carrier_id=str(carrier.id), name=carrier.name, code=carrier.code_norm)
    return CarrierResponse.model_validate(carrier)


@router.get("", response_model=list[CarrierResponse])
async def list_carriers(
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[CarrierResponse]:
    """List all global carriers (not tenant-scoped)."""
    rows = (
        await db.execute(select(Carrier).order_by(Carrier.name))
    ).scalars().all()
    return [CarrierResponse.model_validate(c) for c in rows]


@router.get("/{carrier_id}/aliases", response_model=list[AliasResponse])
async def list_aliases(
    carrier_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[AliasResponse]:
    """List all tenant-specific aliases for a carrier."""
    # Verify carrier exists
    carrier = (
        await db.execute(select(Carrier).where(Carrier.id == carrier_id))
    ).scalar_one_or_none()
    if carrier is None:
        raise HTTPException(status_code=404, detail="Carrier not found")

    rows = (
        await db.execute(
            select(CarrierAlias)
            .where(CarrierAlias.carrier_id == carrier_id)
            .order_by(CarrierAlias.alias_text)
        )
    ).scalars().all()
    return [AliasResponse.model_validate(a) for a in rows]


@router.post("/{carrier_id}/aliases", response_model=AliasResponse, status_code=201)
async def create_alias(
    carrier_id: UUID,
    body: CreateAliasRequest,
    request: Request,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> AliasResponse:
    """Create a new alias for a carrier (tenant-scoped)."""
    carrier = (
        await db.execute(select(Carrier).where(Carrier.id == carrier_id))
    ).scalar_one_or_none()
    if carrier is None:
        raise HTTPException(status_code=404, detail="Carrier not found")

    tenant_id = getattr(request.state, "tenant_id", None)
    alias = CarrierAlias(
        tenant_id=tenant_id,
        carrier_id=carrier_id,
        alias_text=body.alias_text.strip(),
    )
    db.add(alias)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Alias already exists for this tenant and carrier",
        )

    logger.info("carrier_alias_created", carrier_id=str(carrier_id), alias=body.alias_text)
    return AliasResponse.model_validate(alias)


@router.delete("/{carrier_id}/aliases/{alias_text}", status_code=204)
async def delete_alias(
    carrier_id: UUID,
    alias_text: str,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> None:
    """Delete a carrier alias for the current tenant."""
    result = await db.execute(
        delete(CarrierAlias).where(
            CarrierAlias.carrier_id == carrier_id,
            CarrierAlias.alias_text == alias_text,
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="Alias not found")
    logger.info("carrier_alias_deleted", carrier_id=str(carrier_id), alias=alias_text)
