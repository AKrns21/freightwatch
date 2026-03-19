"""Upload router — file ingestion endpoint.

POST /api/uploads                → create upload record + enqueue background processing
GET  /api/uploads                → list uploads (optional ?project_id=)
GET  /api/uploads/{upload_id}    → poll status
GET  /api/uploads/{upload_id}/detail  → full upload record (all DB fields + shipments)
GET  /api/uploads/{upload_id}/file    → download the original file
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.tenant_middleware import get_current_tenant_db
from app.models.database import Shipment, Upload
from app.services.upload_processor_service import get_upload_processor
from app.utils.hash import sha256_bytes

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class UploadResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    upload_id: UUID
    status: str
    filename: str
    file_hash: str


class UploadStatusResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    upload_id: UUID
    status: str
    filename: str
    parse_method: str | None = None
    confidence: float | None = None
    error: dict | None = None
    issues: list | None = None


class UploadListItemResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    tenant_id: UUID
    project_id: UUID | None = None
    filename: str
    file_hash: str
    mime_type: str | None = None
    source_type: str | None = None
    status: str | None = None
    parse_method: str | None = None
    confidence: float | None = None
    received_at: datetime | None = None


class UploadDetailResponse(BaseModel):
    """Full upload record — all DB fields exposed for manual review."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    tenant_id: UUID
    project_id: UUID | None = None
    filename: str
    file_hash: str
    raw_text_hash: str | None = None
    mime_type: str | None = None
    source_type: str | None = None
    doc_type: str | None = None
    storage_url: str | None = None
    status: str | None = None
    parse_method: str | None = None
    confidence: float | None = None
    llm_analysis: dict[str, Any] | None = None
    parse_errors: dict[str, Any] | None = None
    parsing_issues: list[Any] | None = None
    suggested_mappings: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    received_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    shipment_count: int = 0


class ShipmentSummary(BaseModel):
    """Lightweight shipment summary for the upload detail view."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: UUID
    shipment_date: date | None = Field(None, validation_alias="date")
    reference_number: str | None = None
    origin_zip: str | None = None
    dest_zip: str | None = None
    weight_kg: Decimal | None = None
    currency: str | None = None
    actual_total_amount: Decimal | None = None
    completeness_score: Decimal | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[UploadListItemResponse])
async def list_uploads(
    project_id: UUID | None = None,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[UploadListItemResponse]:
    """List uploads for the current tenant, optionally filtered by project."""
    q = select(Upload).order_by(Upload.received_at.desc())
    if project_id is not None:
        q = q.where(Upload.project_id == project_id)
    rows = (await db.execute(q)).scalars().all()
    return [UploadListItemResponse.model_validate(u) for u in rows]


@router.post("", response_model=UploadResponse, status_code=202)
async def create_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    project_id: UUID | None = Form(None),
    source_type: str | None = Form(None),
    db: AsyncSession = Depends(get_current_tenant_db),
) -> UploadResponse:
    """Receive a file, create an upload record, and kick off background processing.

    Returns 202 Accepted immediately; poll GET /uploads/{id} for status updates.
    Duplicate uploads (same file_hash + tenant) return the existing record without
    re-processing.
    """

    content = await file.read()
    file_hash = sha256_bytes(content)

    # Extract tenant_id from the DB session state (set by get_current_tenant_db)
    # We need the raw tenant_id UUID for the background task — read it from the
    # upload once persisted.

    # Deduplication check
    existing = (
        await db.execute(
            select(Upload).where(Upload.file_hash == file_hash)
        )
    ).scalar_one_or_none()

    if existing is not None:
        logger.info(
            "upload_deduplicated",
            upload_id=str(existing.id),
            filename=file.filename,
            file_hash=file_hash,
        )
        return UploadResponse(
            upload_id=existing.id,
            status=existing.status or "pending",
            filename=existing.filename,
            file_hash=existing.file_hash,
        )

    # Persist upload record
    # Store file to a temp location; real storage layer can be added later
    storage_dir = Path("uploads")
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / f"{file_hash}{Path(file.filename or 'upload').suffix}"
    storage_path.write_bytes(content)

    tenant_id_str = getattr(request.state, "tenant_id", None)
    upload = Upload(
        tenant_id=tenant_id_str,
        filename=file.filename or "upload",
        file_hash=file_hash,
        mime_type=file.content_type,
        source_type=source_type,
        project_id=project_id,
        storage_url=str(storage_path),
        status="pending",
    )
    db.add(upload)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A file with the same hash already exists for this tenant.",
        )

    upload_id: UUID = upload.id
    tenant_id: UUID = upload.tenant_id

    logger.info(
        "upload_created",
        upload_id=str(upload_id),
        filename=file.filename,
        file_hash=file_hash,
    )

    # Commit NOW — same race-condition fix as reprocess: background tasks run
    # before the Depends generator commits, so the INSERT row lock must be
    # released before the background task's first UPDATE upload.
    await db.commit()

    # Enqueue background processing
    processor = get_upload_processor()
    background_tasks.add_task(processor.process_upload, upload_id, tenant_id)

    return UploadResponse(
        upload_id=upload_id,
        status="pending",
        filename=upload.filename,
        file_hash=file_hash,
    )


@router.get("/{upload_id}", response_model=UploadStatusResponse)
async def get_upload_status(
    upload_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> UploadStatusResponse:
    """Poll processing status for an upload."""
    upload = (
        await db.execute(select(Upload).where(Upload.id == upload_id))
    ).scalar_one_or_none()

    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    return UploadStatusResponse(
        upload_id=upload.id,
        status=upload.status or "pending",
        filename=upload.filename,
        parse_method=upload.parse_method,
        confidence=float(upload.confidence) if upload.confidence is not None else None,
        error=upload.parse_errors,  # type: ignore[arg-type]
        issues=upload.parsing_issues,  # type: ignore[arg-type]
    )


@router.post("/{upload_id}/reprocess", response_model=UploadStatusResponse, status_code=202)
async def reprocess_upload(
    upload_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> UploadStatusResponse:
    """Reset an upload to 'pending' and re-run the processing pipeline.

    Uses an atomic UPDATE … WHERE status != 'parsing' RETURNING … to avoid
    blocking on a row lock when a background task is actively processing.
    Returns 409 immediately if the upload is already being processed.
    """
    from datetime import UTC, datetime

    from sqlalchemy import update as sa_update

    # Verify upload exists and file is on disk before touching status
    upload = (
        await db.execute(select(Upload).where(Upload.id == upload_id))
    ).scalar_one_or_none()

    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    if not upload.storage_url or not Path(upload.storage_url).exists():
        raise HTTPException(
            status_code=409,
            detail="Original file no longer on disk — re-upload required",
        )

    # Fail fast if the row is locked by an orphaned Postgres backend (e.g. from
    # a previous timed-out reprocess).  Without lock_timeout the UPDATE blocks
    # for command_timeout seconds (25 s), returning a confusing 500 error.
    from sqlalchemy import text as sa_text
    await db.execute(sa_text("SET LOCAL lock_timeout = '3s'"))

    # Atomic reset: only succeeds when status != 'parsing'.
    # No separate SELECT-then-UPDATE race — if a background task just set
    # status='parsing', this UPDATE matches 0 rows and we return 409.
    result = await db.execute(
        sa_update(Upload)
        .where(Upload.id == upload_id, Upload.status != "parsing")
        .values(
            status="pending",
            parse_method=None,
            confidence=None,
            parse_errors=None,
            parsing_issues=None,
            doc_type=None,
            updated_at=datetime.now(UTC),
        )
        .returning(Upload.tenant_id, Upload.filename)
    )
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=409, detail="Upload is already being processed")

    tenant_id: UUID = row.tenant_id
    filename: str = row.filename

    # Commit NOW to release the row lock before the background task starts.
    # FastAPI runs BackgroundTasks inside response.__call__, before the Depends
    # generator cleanup (session.commit()), so without this explicit commit the
    # background task's UPDATE upload blocks on the lock held by this session.
    await db.commit()

    processor = get_upload_processor()
    background_tasks.add_task(processor.process_upload, upload_id, tenant_id)

    logger.info(
        "upload_reprocess_queued",
        upload_id=str(upload_id),
        previous_status=upload.status,
    )

    return UploadStatusResponse(
        upload_id=upload_id,
        status="pending",
        filename=filename,
        parse_method=None,
        confidence=None,
        error=None,
        issues=None,
    )


@router.get("/{upload_id}/detail", response_model=UploadDetailResponse)
async def get_upload_detail(
    upload_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> UploadDetailResponse:
    """Return all DB fields for an upload plus a count of parsed shipments.

    Used by the manual-review detail view to compare what the system stored
    against the original file.
    """
    upload = (
        await db.execute(select(Upload).where(Upload.id == upload_id))
    ).scalar_one_or_none()

    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    shipment_count = len(
        (
            await db.execute(
                select(Shipment.id).where(Shipment.upload_id == upload_id)
            )
        )
        .scalars()
        .all()
    )

    detail = UploadDetailResponse.model_validate(upload)
    detail.shipment_count = shipment_count
    return detail


@router.get("/{upload_id}/shipments", response_model=list[ShipmentSummary])
async def list_upload_shipments(
    upload_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[ShipmentSummary]:
    """Return all shipments parsed from a specific upload."""
    upload = (
        await db.execute(select(Upload.id).where(Upload.id == upload_id))
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    rows = (
        await db.execute(
            select(Shipment)
            .where(Shipment.upload_id == upload_id)
            .order_by(Shipment.date)
        )
    ).scalars().all()
    return [ShipmentSummary.model_validate(s) for s in rows]


@router.get("/{upload_id}/file")
async def download_upload_file(
    upload_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> FileResponse:
    """Serve the original uploaded file for download / manual comparison."""
    upload = (
        await db.execute(select(Upload).where(Upload.id == upload_id))
    ).scalar_one_or_none()

    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    if not upload.storage_url:
        raise HTTPException(status_code=404, detail="No file stored for this upload")

    file_path = Path(upload.storage_url)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=str(file_path),
        filename=upload.filename,
        media_type=upload.mime_type or "application/octet-stream",
    )
