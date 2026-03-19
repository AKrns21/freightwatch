"""Projects router — CRUD for projects, notes, and report listing.

GET    /api/projects                       → list projects
POST   /api/projects                       → create project
GET    /api/projects/{id}                  → get project
PUT    /api/projects/{id}                  → update project
DELETE /api/projects/{id}                  → soft-delete project
GET    /api/projects/{id}/notes            → list consultant notes
POST   /api/projects/{id}/notes            → create note
PUT    /api/projects/{id}/notes/{note_id}  → update note
POST   /api/projects/{id}/notes/{note_id}/resolve → resolve note
GET    /api/projects/{id}/reports          → list reports

Port of backend_legacy/src/modules/project/project.controller.ts
Issue: #50
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.tenant_middleware import get_current_tenant_db
from app.models.database import ConsultantNote, Project, Report, Shipment, Upload

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class ProjectResponse(_CamelModel):
    id: UUID
    name: str
    customer_name: str | None = None
    phase: str | None = None
    status: str | None = None
    consultant_id: UUID | None = None
    project_metadata: dict[str, Any] | None = Field(None, alias="metadata")
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class CreateProjectRequest(_CamelModel):
    name: str = Field(..., max_length=255)
    customer_name: str | None = Field(None, max_length=255)
    phase: str | None = None
    status: str | None = None
    consultant_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class UpdateProjectRequest(_CamelModel):
    name: str | None = Field(None, max_length=255)
    customer_name: str | None = Field(None, max_length=255)
    phase: str | None = None
    status: str | None = None
    consultant_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class NoteResponse(_CamelModel):
    id: UUID
    project_id: UUID
    note_type: str
    content: str
    related_upload_id: UUID | None = None
    related_shipment_id: UUID | None = None
    priority: str | None = None
    status: str | None = None
    created_by: UUID
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class CreateNoteRequest(_CamelModel):
    note_type: str
    content: str = Field(..., max_length=5000)
    related_upload_id: UUID | None = None
    related_shipment_id: UUID | None = None
    priority: str | None = None


class UpdateNoteRequest(_CamelModel):
    note_type: str | None = None
    content: str | None = Field(None, max_length=5000)
    priority: str | None = None
    status: str | None = None


class ReportResponse(_CamelModel):
    id: UUID
    project_id: UUID
    version: int
    report_type: str
    title: str | None = None
    shipment_count: int | None = None
    date_range_start: Any | None = None
    date_range_end: Any | None = None
    generated_at: datetime | None = None


class ProjectStatsResponse(_CamelModel):
    project_id: UUID
    upload_count: int
    shipment_count: int
    note_count: int
    report_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_project_or_404(db: AsyncSession, project_id: UUID) -> Project:
    project = (
        await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _project_to_response(p: Project) -> ProjectResponse:
    return ProjectResponse.model_validate(p)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[ProjectResponse]:
    """List all non-deleted projects for the current tenant."""
    rows = (
        await db.execute(
            select(Project)
            .where(Project.deleted_at.is_(None))
            .order_by(Project.created_at.desc())
        )
    ).scalars().all()
    return [_project_to_response(p) for p in rows]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    db: AsyncSession = Depends(get_current_tenant_db),
    request: Request = None,  # type: ignore[assignment]
) -> ProjectResponse:
    """Create a new project."""
    project = Project(
        name=body.name,
        customer_name=body.customer_name,
        phase=body.phase or "quick_check",
        status=body.status or "draft",
        consultant_id=body.consultant_id,
        project_metadata=body.metadata or {},
    )
    db.add(project)
    await db.flush()
    logger.info("project_created", project_id=str(project.id), name=project.name)
    return _project_to_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> ProjectResponse:
    """Get a single project by ID."""
    project = await _get_project_or_404(db, project_id)
    return _project_to_response(project)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    body: UpdateProjectRequest,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> ProjectResponse:
    """Update a project's fields."""
    project = await _get_project_or_404(db, project_id)

    if body.name is not None:
        project.name = body.name
    if body.customer_name is not None:
        project.customer_name = body.customer_name
    if body.phase is not None:
        project.phase = body.phase
    if body.status is not None:
        project.status = body.status
    if body.consultant_id is not None:
        project.consultant_id = body.consultant_id
    if body.metadata is not None:
        project.project_metadata = body.metadata

    await db.flush()
    logger.info("project_updated", project_id=str(project_id))
    return _project_to_response(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> None:
    """Soft-delete a project."""
    project = await _get_project_or_404(db, project_id)
    project.deleted_at = datetime.utcnow()
    await db.flush()
    logger.info("project_deleted", project_id=str(project_id))


@router.get("/{project_id}/stats", response_model=ProjectStatsResponse)
async def get_project_stats(
    project_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> ProjectStatsResponse:
    """Return aggregate statistics for a project."""
    await _get_project_or_404(db, project_id)

    upload_count = (
        await db.execute(
            select(func.count()).select_from(Upload).where(Upload.project_id == project_id)
        )
    ).scalar_one()

    shipment_count = (
        await db.execute(
            select(func.count()).select_from(Shipment).where(
                Shipment.project_id == project_id,
                Shipment.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    note_count = (
        await db.execute(
            select(func.count()).select_from(ConsultantNote).where(
                ConsultantNote.project_id == project_id
            )
        )
    ).scalar_one()

    report_count = (
        await db.execute(
            select(func.count()).select_from(Report).where(Report.project_id == project_id)
        )
    ).scalar_one()

    return ProjectStatsResponse(
        project_id=project_id,
        upload_count=upload_count,
        shipment_count=shipment_count,
        note_count=note_count,
        report_count=report_count,
    )


# ---------------------------------------------------------------------------
# Notes sub-resource
# ---------------------------------------------------------------------------


@router.get("/{project_id}/notes", response_model=list[NoteResponse])
async def list_notes(
    project_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[NoteResponse]:
    """List all consultant notes for a project."""
    await _get_project_or_404(db, project_id)
    rows = (
        await db.execute(
            select(ConsultantNote)
            .where(ConsultantNote.project_id == project_id)
            .order_by(ConsultantNote.created_at.desc())
        )
    ).scalars().all()
    return [NoteResponse.model_validate(n) for n in rows]


@router.post("/{project_id}/notes", response_model=NoteResponse, status_code=201)
async def create_note(
    project_id: UUID,
    body: CreateNoteRequest,
    request: Request,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> NoteResponse:
    """Create a consultant note on a project."""
    await _get_project_or_404(db, project_id)

    user_id: str | None = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    note = ConsultantNote(
        project_id=project_id,
        note_type=body.note_type,
        content=body.content,
        related_upload_id=body.related_upload_id,
        related_shipment_id=body.related_shipment_id,
        priority=body.priority or "medium",
        status="open",
        created_by=UUID(user_id),
    )
    db.add(note)
    await db.flush()
    logger.info("note_created", note_id=str(note.id), project_id=str(project_id))
    return NoteResponse.model_validate(note)


@router.put("/{project_id}/notes/{note_id}", response_model=NoteResponse)
async def update_note(
    project_id: UUID,
    note_id: UUID,
    body: UpdateNoteRequest,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> NoteResponse:
    """Update a consultant note."""
    await _get_project_or_404(db, project_id)

    note = (
        await db.execute(
            select(ConsultantNote).where(
                ConsultantNote.id == note_id,
                ConsultantNote.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    if body.note_type is not None:
        note.note_type = body.note_type
    if body.content is not None:
        note.content = body.content
    if body.priority is not None:
        note.priority = body.priority
    if body.status is not None:
        note.status = body.status

    await db.flush()
    return NoteResponse.model_validate(note)


@router.post("/{project_id}/notes/{note_id}/resolve", response_model=NoteResponse)
async def resolve_note(
    project_id: UUID,
    note_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> NoteResponse:
    """Mark a consultant note as resolved."""
    await _get_project_or_404(db, project_id)

    note = (
        await db.execute(
            select(ConsultantNote).where(
                ConsultantNote.id == note_id,
                ConsultantNote.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    note.status = "resolved"
    note.resolved_at = datetime.utcnow()
    await db.flush()
    logger.info("note_resolved", note_id=str(note_id))
    return NoteResponse.model_validate(note)


# ---------------------------------------------------------------------------
# Reports sub-resource (read-only listing — generation via POST /reports)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/reports", response_model=list[ReportResponse])
async def list_reports(
    project_id: UUID,
    db: AsyncSession = Depends(get_current_tenant_db),
) -> list[ReportResponse]:
    """List all reports for a project, ordered newest first."""
    await _get_project_or_404(db, project_id)
    rows = (
        await db.execute(
            select(Report)
            .where(Report.project_id == project_id)
            .order_by(Report.version.desc())
        )
    ).scalars().all()
    return [ReportResponse.model_validate(r) for r in rows]
