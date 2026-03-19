"""Upload router — file ingestion endpoint.

POST /api/uploads                → create upload record + enqueue background processing
GET  /api/uploads                → list uploads (optional ?project_id=)
GET  /api/uploads/{upload_id}    → poll status
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.tenant_middleware import get_current_tenant_db
from app.models.database import Upload
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
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

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
    parsing_issues: list | None = None
    received_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[UploadListItemResponse])
async def list_uploads(
    project_id: UUID | None = None,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[UploadListItemResponse]:
    """List uploads for the current tenant, optionally filtered by project."""
    q = select(Upload).order_by(Upload.created_at.desc())
    if project_id is not None:
        q = q.where(Upload.project_id == project_id)
    rows = (await db.execute(q)).scalars().all()
    return [UploadListItemResponse.model_validate(u) for u in rows]


@router.post("", response_model=UploadResponse, status_code=202)
async def create_upload(
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
    from pathlib import Path

    # Store file to a temp location; real storage layer can be added later
    storage_dir = Path("uploads")
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / f"{file_hash}{Path(file.filename or 'upload').suffix}"
    storage_path.write_bytes(content)

    upload = Upload(
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
