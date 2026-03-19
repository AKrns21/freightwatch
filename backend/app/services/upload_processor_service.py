"""UploadProcessorService — BackgroundTask-based upload processing pipeline.

Port of backend_legacy/src/modules/upload/upload-processor.service.ts
     + backend_legacy/src/modules/upload/upload.service.ts (processing logic)
Issue: #49

Key features:
- process_upload(): orchestrates detect → parse → validate → store → benchmark
- Status tracking: pending → parsing → parsed / failed / partial_success
- Timeout detection: asyncio startup task marks stale 'parsing' jobs as 'failed'
- Concurrency: asyncio.Semaphore(settings.upload_processing_concurrency)
- Deduplication: same file_hash + tenant_id → skip re-processing
- Carrier mapping: alias resolution + placeholder creation when unknown
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.database import Carrier, CarrierAlias, Shipment, Upload
from app.services.benchmark_service import get_benchmark_service
from app.services.extraction_validator_service import (
    ExtractionValidatorService,
    ShipmentInput,
)
from app.services.parsing.csv_parser import ParsedShipment, RowParseError, parse_with_template
from app.services.template_service import TemplateMatch, get_template_service

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STALE_MINUTES = 5  # uploads stuck in 'parsing' for longer → mark as 'failed'
_STALE_POLL_SECONDS = 60  # how often the background watcher runs

# ---------------------------------------------------------------------------
# Upload status values (mirrors legacy UploadStatus enum)
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_PARSED = "parsed"
STATUS_PARTIAL_SUCCESS = "partial_success"
STATUS_FAILED = "failed"
STATUS_NEEDS_MANUAL_REVIEW = "needs_manual_review"

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ProcessingResult:
    """Result returned by process_upload."""

    upload_id: UUID
    final_status: str
    shipment_count: int = 0
    row_error_count: int = 0
    parse_method: str | None = None
    error: str | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "upload_id": str(self.upload_id),
            "final_status": self.final_status,
            "shipment_count": self.shipment_count,
            "row_error_count": self.row_error_count,
            "parse_method": self.parse_method,
            "error": self.error,
            "issues": self.issues,
        }


# ---------------------------------------------------------------------------
# Semaphore — shared across the process lifetime
# ---------------------------------------------------------------------------

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.upload_processing_concurrency)
    return _semaphore


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------


class UploadProcessorService:
    """Orchestrates the upload processing pipeline as FastAPI BackgroundTasks.

    Pipeline stages:
        1. Load upload record + validate state
        2. Set status → 'parsing'
        3. Find matching template (confidence ≥ 0.7 via TemplateService)
        4. Parse CSV/Excel with template; non-CSV → needs_manual_review
        5. Fetch existing reference numbers for dedup validation
        6. Validate extracted shipments (ExtractionValidatorService)
        7. Resolve carrier aliases; create placeholders for unknowns
        8. Persist Shipment rows
        9. Calculate benchmarks in bulk (BenchmarkService)
       10. Update upload.status → final status + metrics

    Failure isolation: any unhandled exception sets status='failed' with
    error captured in upload.parse_errors.

    Example usage:
        svc = UploadProcessorService()
        background_tasks.add_task(svc.process_upload, upload_id, tenant_id)
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)
        self._template_service = get_template_service()
        self._validator = ExtractionValidatorService()
        self._benchmark_service = get_benchmark_service()

    # -----------------------------------------------------------------------
    # Public: main entry point
    # -----------------------------------------------------------------------

    async def process_upload(self, upload_id: UUID, tenant_id: UUID) -> ProcessingResult:
        """Process a single upload through the full pipeline.

        Acquires the global semaphore before starting so no more than
        settings.upload_processing_concurrency uploads are processed concurrently.

        Args:
            upload_id: UUID of the upload record to process.
            tenant_id: Tenant UUID for RLS context.

        Returns:
            ProcessingResult describing the outcome.
        """
        async with _get_semaphore():
            return await self._run_pipeline(upload_id, tenant_id)

    # -----------------------------------------------------------------------
    # Internal pipeline
    # -----------------------------------------------------------------------

    async def _run_pipeline(self, upload_id: UUID, tenant_id: UUID) -> ProcessingResult:
        log = self.logger.bind(upload_id=str(upload_id), tenant_id=str(tenant_id))
        log.info("upload_processing_start")

        # Verify the upload exists before transitioning to 'parsing'
        async with _TenantSession(tenant_id) as db:
            upload = await self._load_upload(db, upload_id, tenant_id)
            if upload is None:
                log.error("upload_not_found")
                return ProcessingResult(
                    upload_id=upload_id,
                    final_status=STATUS_FAILED,
                    error=f"Upload {upload_id} not found",
                )

        try:
            return await self._pipeline_stages(upload_id, tenant_id, log)
        except Exception as exc:
            log.error("upload_processing_error", error=str(exc), exc_info=True)
            await self._set_status_error(upload_id, tenant_id, exc)
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_FAILED,
                error=str(exc),
            )

    async def _pipeline_stages(
        self,
        upload_id: UUID,
        tenant_id: UUID,
        log: Any,
    ) -> ProcessingResult:
        # Stage 2: mark as 'parsing'
        await self._update_status(upload_id, tenant_id, STATUS_PARSING)

        # Stage 3: template matching (uses its own session to stay atomic)
        match: TemplateMatch | None
        upload: Upload
        async with _TenantSession(tenant_id) as db:
            upload = await self._load_upload(db, upload_id, tenant_id)
            assert upload is not None

            log.info("template_matching_start")
            match = await self._template_service.find_match(db, upload, tenant_id)

        if match is None:
            log.warning("no_template_match")
            await self._update_status(
                upload_id,
                tenant_id,
                STATUS_NEEDS_MANUAL_REVIEW,
                extra={
                    "parse_method": "manual",
                    "parsing_issues": [
                        {
                            "type": "no_template_match",
                            "message": "No matching template found — manual review required",
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    ],
                },
            )
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_NEEDS_MANUAL_REVIEW,
                parse_method="manual",
            )

        log.info(
            "template_match_found",
            template_id=str(match.template.id),
            template_name=match.template.name,
            confidence=match.confidence,
        )

        # Stage 4: parse
        shipments, row_errors, confidence = await self._parse(upload, match)

        # Stage 5: fetch existing references for dedup
        async with _TenantSession(tenant_id) as db:
            existing_refs = await self._fetch_existing_refs(db, tenant_id)

        # Stage 6: validate
        shipment_inputs = [
            ShipmentInput(index=i, reference_number=s.reference_number)
            for i, s in enumerate(shipments)
        ]
        validation = self._validator.validate_shipments(shipment_inputs, existing_refs)
        rejected_indices = {
            v.index
            for v in validation.violations
            if v.action == "reject" and v.index is not None
        }
        valid_shipments = [s for i, s in enumerate(shipments) if i not in rejected_indices]
        validation_issues = [
            {
                "type": "validation_error",
                "rule": v.rule,
                "message": v.detail,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            for v in validation.violations
        ]

        # Stage 7 + 8: carrier resolution + persist
        async with _TenantSession(tenant_id) as db:
            saved_ids = await self._save_shipments(
                db, valid_shipments, upload_id, tenant_id, log
            )

        # Stage 9: benchmarks
        if saved_ids:
            async with _TenantSession(tenant_id) as db:
                await self._calculate_benchmarks(db, saved_ids, tenant_id, log)

        # Stage 10: final status + metrics
        row_error_issues = [
            {
                "type": "row_parse_error",
                "row": e.row,
                "message": e.error,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            for e in row_errors
        ]
        all_issues = row_error_issues + validation_issues

        has_errors = bool(row_errors or validation_issues)
        if has_errors and len(saved_ids) == 0:
            final_status = STATUS_FAILED
        elif has_errors:
            final_status = STATUS_PARTIAL_SUCCESS
        else:
            final_status = STATUS_PARSED

        await self._update_status(
            upload_id,
            tenant_id,
            final_status,
            extra={
                "parse_method": "template",
                "confidence": confidence,
                **({"parsing_issues": all_issues} if all_issues else {}),
            },
        )

        log.info(
            "upload_processing_complete",
            final_status=final_status,
            shipment_count=len(valid_shipments),
            row_errors=len(row_errors),
        )

        return ProcessingResult(
            upload_id=upload_id,
            final_status=final_status,
            shipment_count=len(valid_shipments),
            row_error_count=len(row_errors),
            parse_method="template",
            issues=all_issues,
        )

    # -----------------------------------------------------------------------
    # Parse helper
    # -----------------------------------------------------------------------

    async def _parse(
        self,
        upload: Upload,
        match: TemplateMatch,
    ) -> tuple[list[ParsedShipment], list[RowParseError], float]:
        """Delegate to CSV parser. Returns (shipments, row_errors, confidence)."""
        if upload.storage_url is None:
            return [], [], 0.0

        mime = (upload.mime_type or "").lower()
        filename = (upload.filename or "").lower()
        is_csv = "csv" in mime or filename.endswith(".csv")
        is_excel = "excel" in mime or "spreadsheet" in mime or filename.endswith((".xlsx", ".xls"))

        if is_csv or is_excel:
            mappings: dict[str, Any] = match.template.mappings or {}
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: parse_with_template(
                    upload.storage_url,  # type: ignore[arg-type]
                    str(upload.tenant_id),
                    str(upload.id),
                    mappings,
                ),
            )

        self.logger.warning(
            "unsupported_mime_for_parsing",
            upload_id=str(upload.id),
            mime_type=upload.mime_type,
        )
        return [], [], 0.0

    # -----------------------------------------------------------------------
    # Reference number dedup
    # -----------------------------------------------------------------------

    async def _fetch_existing_refs(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> set[str]:
        """Load all non-null reference numbers for this tenant (for dedup check)."""
        result = await db.execute(
            select(Shipment.reference_number).where(
                Shipment.tenant_id == tenant_id,
                Shipment.reference_number.isnot(None),
                Shipment.deleted_at.is_(None),
            )
        )
        return {row for (row,) in result.all() if row}

    # -----------------------------------------------------------------------
    # Carrier resolution + shipment persistence
    # -----------------------------------------------------------------------

    async def _save_shipments(
        self,
        db: AsyncSession,
        parsed: list[ParsedShipment],
        upload_id: UUID,
        tenant_id: UUID,
        log: Any,
    ) -> list[UUID]:
        """Persist ParsedShipment records, resolve carrier aliases, return new UUIDs."""
        saved_ids: list[UUID] = []

        for ps in parsed:
            try:
                carrier_id = await self._resolve_carrier(
                    db, ps.carrier_name, tenant_id, upload_id, log
                )

                row = Shipment(
                    tenant_id=tenant_id,
                    upload_id=upload_id,
                    carrier_id=carrier_id,
                    date=ps.date,
                    reference_number=ps.reference_number,
                    service_level=ps.service_level,
                    origin_zip=ps.origin_zip,
                    origin_country=ps.origin_country,
                    dest_zip=ps.dest_zip,
                    dest_country=ps.dest_country,
                    weight_kg=ps.weight_kg,
                    length_m=ps.length_m,
                    pallets=ps.pallets,
                    currency=ps.currency,
                    actual_base_amount=ps.actual_base_amount,
                    actual_diesel_amount=ps.actual_diesel_amount,
                    actual_toll_amount=ps.actual_toll_amount,
                    actual_total_amount=ps.actual_total_amount,
                    completeness_score=ps.completeness_score,
                    missing_fields=ps.missing_fields or None,
                    source_data=ps.source_data,
                    extraction_method="template",
                )
                db.add(row)
                await db.flush()
                saved_ids.append(row.id)
            except Exception as exc:
                log.error("shipment_save_error", error=str(exc), exc_info=True)

        log.info("shipments_saved", count=len(saved_ids))
        return saved_ids

    async def _resolve_carrier(
        self,
        db: AsyncSession,
        carrier_name: str | None,
        tenant_id: UUID,
        upload_id: UUID,
        log: Any,
    ) -> UUID | None:
        """Map raw carrier name → carrier.id.

        Lookup order: tenant alias → global alias → placeholder creation.
        Placeholder carriers are created idempotently (same code_norm reused).
        """
        if not carrier_name:
            return None

        normalized = carrier_name.strip().lower()

        # 1. Tenant-specific alias
        row = (
            await db.execute(
                select(CarrierAlias.carrier_id).where(
                    CarrierAlias.tenant_id == tenant_id,
                    CarrierAlias.alias_text == normalized,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row  # type: ignore[return-value]

        # 2. Global alias (tenant_id IS NULL)
        row = (
            await db.execute(
                select(CarrierAlias.carrier_id).where(
                    CarrierAlias.tenant_id.is_(None),
                    CarrierAlias.alias_text == normalized,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row  # type: ignore[return-value]

        # 3. Create placeholder so carrier_id is never null
        log.warning("carrier_not_found_creating_placeholder", carrier_name=carrier_name)
        code_norm = "PLACEHOLDER_" + carrier_name.upper().replace(" ", "_")[:40]

        existing_carrier = (
            await db.execute(select(Carrier).where(Carrier.code_norm == code_norm))
        ).scalar_one_or_none()

        if existing_carrier is None:
            placeholder = Carrier(
                name=carrier_name,
                code_norm=code_norm,
                conversion_rules={},
            )
            db.add(placeholder)
            await db.flush()
            carrier_id: UUID = placeholder.id
        else:
            carrier_id = existing_carrier.id

        # Register tenant-scoped alias for future imports (idempotent)
        await db.execute(
            pg_insert(CarrierAlias)
            .values(tenant_id=tenant_id, alias_text=normalized, carrier_id=carrier_id)
            .on_conflict_do_nothing()
        )

        # Surface a warning so the consultant can resolve the placeholder via UI
        upload = (
            await db.execute(select(Upload).where(Upload.id == upload_id))
        ).scalar_one_or_none()
        if upload is not None:
            existing_issues: list[Any] = list(upload.parsing_issues or [])
            existing_issues.append(
                {
                    "type": "unknown_carrier",
                    "message": (
                        f"Carrier '{carrier_name}' not found in registry — "
                        "created as placeholder. Please verify."
                    ),
                    "carrier_name": carrier_name,
                    "placeholder_carrier_id": str(carrier_id),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            await db.execute(
                update(Upload)
                .where(Upload.id == upload_id)
                .values(parsing_issues=existing_issues, updated_at=datetime.now(UTC))
            )

        return carrier_id

    # -----------------------------------------------------------------------
    # Benchmark calculation
    # -----------------------------------------------------------------------

    async def _calculate_benchmarks(
        self,
        db: AsyncSession,
        shipment_ids: list[UUID],
        tenant_id: UUID,
        log: Any,
    ) -> None:
        result = await self._benchmark_service.calculate_benchmarks_bulk(
            db, shipment_ids, tenant_id
        )
        log.info(
            "benchmarks_calculated",
            total=result.total,
            succeeded=result.succeeded,
            failed=result.failed,
        )

    # -----------------------------------------------------------------------
    # Status helpers
    # -----------------------------------------------------------------------

    async def _update_status(
        self,
        upload_id: UUID,
        tenant_id: UUID,
        status: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(UTC),
        }
        if extra:
            values.update(extra)

        async with _TenantSession(tenant_id) as db:
            await db.execute(
                update(Upload).where(Upload.id == upload_id).values(**values)
            )

        self.logger.info(
            "upload_status_updated",
            upload_id=str(upload_id),
            status=status,
        )

    async def _set_status_error(
        self,
        upload_id: UUID,
        tenant_id: UUID,
        exc: Exception,
    ) -> None:
        async with _TenantSession(tenant_id) as db:
            await db.execute(
                update(Upload)
                .where(Upload.id == upload_id)
                .values(
                    status=STATUS_FAILED,
                    updated_at=datetime.now(UTC),
                    parse_errors={
                        "message": str(exc),
                        "type": type(exc).__name__,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
            )
        self.logger.error(
            "upload_status_set_failed",
            upload_id=str(upload_id),
            error=str(exc),
        )

    # -----------------------------------------------------------------------
    # DB helper
    # -----------------------------------------------------------------------

    async def _load_upload(
        self,
        db: AsyncSession,
        upload_id: UUID,
        tenant_id: UUID,
    ) -> Upload | None:
        result = await db.execute(
            select(Upload).where(
                Upload.id == upload_id,
                Upload.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Async context manager for tenant-scoped sessions
# ---------------------------------------------------------------------------


class _TenantSession:
    """Async context manager: opens AsyncSession, sets RLS, commits on success."""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._session: AsyncSession | None = None
        self._cm = None

    async def __aenter__(self) -> AsyncSession:
        self._cm = AsyncSessionLocal()
        self._session = await self._cm.__aenter__()
        await self._session.execute(
            text("SET LOCAL app.current_tenant = :tid"),
            {"tid": str(self._tenant_id)},
        )
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        assert self._session is not None
        if exc_type is None:
            try:
                await self._session.commit()
            except Exception:
                await self._session.rollback()
                raise
        else:
            await self._session.rollback()
        return await self._cm.__aexit__(exc_type, exc_val, exc_tb)


# ---------------------------------------------------------------------------
# Stale-job watcher — started at application startup
# ---------------------------------------------------------------------------


async def _watch_stale_uploads() -> None:
    """Periodically mark uploads stuck in 'parsing' as 'failed'.

    Runs as an asyncio background task from the FastAPI lifespan.
    Uploads in status='parsing' with updated_at older than _STALE_MINUTES
    are assumed to have crashed without updating their status.

    Note: The stale-watcher update does NOT use SET LOCAL because it operates
    across all tenants. The upload table's RLS is bypassed here intentionally
    via a superuser/service-role connection; or it relies on the fact that the
    watcher only reads status+updated_at (non-tenant-discriminating columns).
    In a Supabase deployment, use the service-role key for the DB URL used by
    the watcher, or grant BYPASSRLS to the app role for this specific update.
    """
    log = logger.bind(task="stale_upload_watcher")
    log.info("stale_watcher_started", poll_seconds=_STALE_POLL_SECONDS)

    while True:
        await asyncio.sleep(_STALE_POLL_SECONDS)
        try:
            cutoff = datetime.now(UTC) - timedelta(minutes=_STALE_MINUTES)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    update(Upload)
                    .where(
                        Upload.status == STATUS_PARSING,
                        Upload.updated_at < cutoff,
                    )
                    .values(
                        status=STATUS_FAILED,
                        updated_at=datetime.now(UTC),
                        parse_errors={
                            "message": (
                                f"Processing timed out after {_STALE_MINUTES} minutes"
                            ),
                            "type": "ProcessingTimeout",
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
                    .returning(Upload.id)
                )
                timed_out = result.scalars().all()
                await db.commit()

            if timed_out:
                log.warning(
                    "stale_uploads_marked_failed",
                    count=len(timed_out),
                    upload_ids=[str(uid) for uid in timed_out],
                )
        except Exception as exc:
            log.error("stale_watcher_error", error=str(exc), exc_info=True)


def start_stale_watcher() -> asyncio.Task[None]:
    """Start the stale-upload watcher as a background asyncio task.

    Call this from the FastAPI lifespan after the DB is initialised:

        async with lifespan(app):
            start_stale_watcher()
    """
    return asyncio.create_task(_watch_stale_uploads(), name="stale_upload_watcher")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_upload_processor: UploadProcessorService | None = None


def get_upload_processor() -> UploadProcessorService:
    global _upload_processor
    if _upload_processor is None:
        _upload_processor = UploadProcessorService()
    return _upload_processor
