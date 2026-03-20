"""Diesel floater router — manage carrier diesel surcharge history.

GET    /api/diesel-floaters                        → list all (optionally filter by carrier_id)
POST   /api/diesel-floaters                        → create single entry
PUT    /api/diesel-floaters/{id}                   → update entry
DELETE /api/diesel-floaters/{id}                   → delete entry
POST   /api/diesel-floaters/import-csv             → bulk import from CSV text
GET    /api/diesel-floaters/destatis-prices        → list cached Destatis prices
POST   /api/diesel-floaters/destatis-prices/fetch  → fetch N months of history from Destatis

Issue: #62
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.tenant_middleware import get_current_tenant_db
from app.models.database import Carrier, DestatisDieselPrice, DieselFloater
from app.services.destatis_service import get_destatis_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/diesel-floaters", tags=["diesel-floaters"])

_VALID_BASIS = {"base", "base_plus_toll", "total"}

_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")


def _parse_date(value: str) -> date:
    import datetime as _dt
    for fmt in _DATE_FORMATS:
        try:
            if fmt == "%Y-%m-%d":
                return date.fromisoformat(value)
            return _dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {value!r}")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DieselFloaterOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    carrier_id: UUID
    carrier_name: str | None = None
    valid_from: date
    valid_until: date | None = None
    floater_pct: Decimal
    basis: str
    source: str | None = None


class DieselFloaterIn(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    carrier_id: UUID
    valid_from: date
    valid_until: date | None = None
    floater_pct: Decimal = Field(gt=Decimal("0"), lt=Decimal("100"))
    basis: str = "base"
    source: str | None = None

    @field_validator("basis")
    @classmethod
    def validate_basis(cls, v: str) -> str:
        if v not in _VALID_BASIS:
            raise ValueError(f"basis must be one of {_VALID_BASIS}")
        return v


class CsvImportRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    carrier_id: UUID
    csv_text: str  # raw CSV content: valid_from,valid_until,floater_pct[,basis][,source]
    source: str | None = None


class CsvImportResult(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    inserted: int
    updated: int
    skipped: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _carrier_exists(db: AsyncSession, carrier_id: UUID) -> bool:
    row = await db.execute(select(Carrier.id).where(Carrier.id == carrier_id))
    return row.scalar_one_or_none() is not None


async def _enrich_with_carrier_name(
    db: AsyncSession, rows: list[DieselFloater]
) -> list[DieselFloaterOut]:
    if not rows:
        return []
    carrier_ids = {r.carrier_id for r in rows}
    carriers = (
        await db.execute(select(Carrier).where(Carrier.id.in_(carrier_ids)))
    ).scalars().all()
    name_map = {c.id: c.name for c in carriers}
    return [
        DieselFloaterOut(
            id=r.id,
            carrier_id=r.carrier_id,
            carrier_name=name_map.get(r.carrier_id),
            valid_from=r.valid_from,
            valid_until=r.valid_until,
            floater_pct=r.floater_pct,
            basis=r.basis or "base",
            source=r.source,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DieselFloaterOut])
async def list_diesel_floaters(
    carrier_id: UUID | None = None,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[DieselFloaterOut]:
    """List all diesel floater entries for this tenant, newest first."""
    q = select(DieselFloater).order_by(DieselFloater.valid_from.desc())
    if carrier_id:
        q = q.where(DieselFloater.carrier_id == carrier_id)
    rows = (await db.execute(q)).scalars().all()
    return await _enrich_with_carrier_name(db, list(rows))


@router.post("", response_model=DieselFloaterOut, status_code=201)
async def create_diesel_floater(
    body: DieselFloaterIn,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> DieselFloaterOut:
    """Create a single diesel floater entry."""
    if not await _carrier_exists(db, body.carrier_id):
        raise HTTPException(status_code=404, detail="Carrier not found")

    from sqlalchemy import text as sa_text
    tenant_id_row = await db.execute(sa_text("SELECT current_setting('app.current_tenant')::uuid"))
    tenant_id: UUID = tenant_id_row.scalar_one()

    entry = DieselFloater(
        tenant_id=tenant_id,
        carrier_id=body.carrier_id,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        floater_pct=body.floater_pct,
        basis=body.basis,
        source=body.source,
    )
    db.add(entry)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Entry for carrier {body.carrier_id} "
                f"with valid_from {body.valid_from} already exists"
            ),
        )

    result = (await _enrich_with_carrier_name(db, [entry]))[0]
    logger.info(
        "diesel_floater_created",
        carrier_id=str(body.carrier_id),
        valid_from=str(body.valid_from),
    )
    return result


@router.put("/{entry_id}", response_model=DieselFloaterOut)
async def update_diesel_floater(
    entry_id: UUID,
    body: DieselFloaterIn,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> DieselFloaterOut:
    """Update an existing diesel floater entry."""
    entry = (
        await db.execute(select(DieselFloater).where(DieselFloater.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    entry.carrier_id = body.carrier_id
    entry.valid_from = body.valid_from
    entry.valid_until = body.valid_until
    entry.floater_pct = body.floater_pct
    entry.basis = body.basis
    entry.source = body.source

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate entry (carrier + valid_from)")

    return (await _enrich_with_carrier_name(db, [entry]))[0]


@router.delete("/{entry_id}", status_code=204)
async def delete_diesel_floater(
    entry_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> None:
    """Delete a diesel floater entry."""
    result = await db.execute(
        delete(DieselFloater).where(DieselFloater.id == entry_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    logger.info("diesel_floater_deleted", entry_id=str(entry_id))


@router.post("/import-csv", response_model=CsvImportResult)
async def import_csv(
    body: CsvImportRequest,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> CsvImportResult:
    """Bulk import diesel floater history from CSV.

    Expected columns (header row required):
        valid_from, valid_until, floater_pct[, basis][, source]

    Dates: DD.MM.YYYY or YYYY-MM-DD.
    On duplicate (carrier + valid_from): updates the existing row.
    """
    if not await _carrier_exists(db, body.carrier_id):
        raise HTTPException(status_code=404, detail="Carrier not found")

    from sqlalchemy import text as sa_text
    tenant_id_row = await db.execute(sa_text("SELECT current_setting('app.current_tenant')::uuid"))
    tenant_id: UUID = tenant_id_row.scalar_one()

    reader = csv.DictReader(io.StringIO(body.csv_text.strip()))
    inserted = updated = skipped = 0
    errors: list[str] = []

    for row_num, row in enumerate(reader, start=2):
        try:
            raw_from = (row.get("valid_from") or "").strip()
            raw_until = (row.get("valid_until") or "").strip()
            raw_pct = (row.get("floater_pct") or "").strip()
            basis = (row.get("basis") or "base").strip() or "base"
            source = (row.get("source") or body.source or "").strip() or None

            if not raw_from or not raw_pct:
                errors.append(f"Row {row_num}: missing valid_from or floater_pct")
                skipped += 1
                continue

            valid_from = _parse_date(raw_from)
            valid_until = _parse_date(raw_until) if raw_until else None
            floater_pct = Decimal(raw_pct.replace(",", "."))

            if basis not in _VALID_BASIS:
                basis = "base"

            # Upsert: update if (tenant, carrier, valid_from) exists
            existing = (
                await db.execute(
                    select(DieselFloater).where(
                        DieselFloater.tenant_id == tenant_id,
                        DieselFloater.carrier_id == body.carrier_id,
                        DieselFloater.valid_from == valid_from,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                existing.valid_until = valid_until
                existing.floater_pct = floater_pct
                existing.basis = basis
                existing.source = source
                updated += 1
            else:
                db.add(
                    DieselFloater(
                        tenant_id=tenant_id,
                        carrier_id=body.carrier_id,
                        valid_from=valid_from,
                        valid_until=valid_until,
                        floater_pct=floater_pct,
                        basis=basis,
                        source=source,
                    )
                )
                inserted += 1

        except Exception as exc:
            errors.append(f"Row {row_num}: {exc}")
            skipped += 1

    await db.flush()

    logger.info(
        "diesel_floater_csv_import",
        carrier_id=str(body.carrier_id),
        inserted=inserted,
        updated=updated,
        skipped=skipped,
    )
    return CsvImportResult(inserted=inserted, updated=updated, skipped=skipped, errors=errors)


# ---------------------------------------------------------------------------
# Destatis reference price cache
# ---------------------------------------------------------------------------


class DestatisPriceOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    year: int
    month: int
    price_ct: Decimal
    series_code: str
    fetched_at: str


class DestatisFetchResult(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    fetched: int
    months_requested: int
    series_code: str


@router.get("/destatis-prices", response_model=list[DestatisPriceOut])
async def list_destatis_prices(
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[DestatisPriceOut]:
    """Return all cached Destatis diesel reference prices, newest first."""
    rows = (
        await db.execute(
            select(DestatisDieselPrice).order_by(
                DestatisDieselPrice.price_year.desc(),
                DestatisDieselPrice.price_month.desc(),
            )
        )
    ).scalars().all()
    return [
        DestatisPriceOut(
            year=r.price_year,
            month=r.price_month,
            price_ct=r.price_ct,
            series_code=r.series_code,
            fetched_at=r.fetched_at.isoformat() if r.fetched_at else "",
        )
        for r in rows
    ]


@router.post("/destatis-prices/fetch", response_model=DestatisFetchResult)
async def fetch_destatis_prices(
    months: int = 36,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> DestatisFetchResult:
    """Fetch up to `months` months of Destatis diesel prices into the cache.

    Skips months already cached. Use months=60 for 5 years of history.
    """
    if months < 1 or months > 120:
        raise HTTPException(status_code=400, detail="months must be between 1 and 120")

    from app.config import settings

    svc = get_destatis_service()
    fetched = await svc.refresh_recent(db, months=months)
    logger.info("destatis_prices_fetched_via_api", fetched=fetched, months=months)
    return DestatisFetchResult(
        fetched=fetched,
        months_requested=months,
        series_code=settings.destatis_diesel_series,
    )
