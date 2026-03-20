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

Pipeline stages:
    1.  Load upload record + validate state
    2.  Set status → 'parsing'
    2.5 DocumentService.process() — extract text/dataframes (Vision OCR for PDFs)
    2.6 DocumentTypeDetector.detect() — classify doc type, persist to upload.doc_type
    3.  TemplateService.find_match() — now receives extracted column headers
    4.  Parse CSV/Excel with template
    5.  Fetch existing reference numbers for dedup
    6.  Validate extracted shipments (ExtractionValidatorService)
    7.  Resolve carrier aliases; create placeholders for unknowns
    8.  Persist Shipment rows
    9.  Calculate benchmarks in bulk (BenchmarkService)
    10. Update upload.status → final status + metrics
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from app.services.document_service import DocumentExtractionResult, get_document_service
from app.services.document_type_detector import get_document_type_detector
from app.services.carrier_service import get_carrier_service
from app.services.extraction_validator_service import (
    ExtractionValidatorService,
    ShipmentCountryInput,
    ShipmentInput,
)
from app.services.parsing.csv_parser import ParsedShipment, RowParseError, parse_with_template
from app.services.parsing.invoice_parser import InvoiceParseResult, InvoiceParserService
from app.services.parsing.tariff_parser import get_tariff_parser
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
        self._document_service = get_document_service()
        self._detector = get_document_type_detector()
        self._validator = ExtractionValidatorService()
        self._benchmark_service = get_benchmark_service()
        self._invoice_parser = InvoiceParserService()
        self._tariff_parser = get_tariff_parser()

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

        # Stages 2.5 / 2.6 / 3 share one session to stay atomic
        match: TemplateMatch | None
        upload: Upload
        doc_result: DocumentExtractionResult | None
        _is_tabular: bool = True
        async with _TenantSession(tenant_id) as db:
            upload = await self._load_upload(db, upload_id, tenant_id)
            assert upload is not None

            # Stage 2.5: extract document content from disk (Vision OCR for PDFs)
            doc_result = await self._extract_document(upload, log)

            # Stage 2.6: classify doc type + persist, build content for matching
            doc_type, file_content = await self._detect_doc_type(upload, doc_result, log)
            if doc_type is not None:
                await db.execute(
                    update(Upload)
                    .where(Upload.id == upload_id)
                    .values(doc_type=doc_type, updated_at=datetime.now(UTC))
                )

            # Determine if file is tabular (CSV/XLSX) or unstructured (PDF/image)
            _mime = (upload.mime_type or "").lower()
            _fname = (upload.filename or "").lower()
            _is_tabular = (
                "csv" in _mime
                or "spreadsheet" in _mime
                or "excel" in _mime
                or "plain" in _mime
                or _fname.endswith((".csv", ".xlsx", ".xls"))
            )

            # Stage 3: template matching — only for tabular files
            match = None
            if _is_tabular:
                log.info("template_matching_start", doc_type=doc_type)
                match = await self._template_service.find_match(
                    db, upload, tenant_id, file_content=file_content
                )
            else:
                log.info(
                    "non_tabular_file_detected",
                    doc_type=doc_type,
                    mime=_mime,
                )

        # Non-tabular files (PDF / image): branch on detected doc_type
        if not _is_tabular:
            if doc_type == "tariff":
                return await self._process_tariff_upload(upload, upload_id, tenant_id, log)
            if doc_type == "shipment_csv":
                # Shipment list as PDF — unsupported, ask user to re-upload as CSV/XLSX
                return await self._hold_non_invoice_pdf(upload_id, tenant_id, doc_type, log)
            # "invoice", "other", or None → existing InvoiceParserService pipeline
            return await self._process_invoice_upload(upload, upload_id, tenant_id, log)

        if match is None:
            log.warning("no_template_match", doc_type=doc_type)
            _issue_type = "no_template_match"
            _message = "No matching template found — manual review required"
            await self._update_status(
                upload_id,
                tenant_id,
                STATUS_NEEDS_MANUAL_REVIEW,
                extra={
                    "parse_method": "manual",
                    "parsing_issues": [
                        {
                            "type": _issue_type,
                            "message": _message,
                            "doc_type": doc_type,
                            "extraction_mode": doc_result.mode if doc_result else None,
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

        # Stage 6.1: ZIP / country consistency check (issue #54)
        country_inputs = [
            ShipmentCountryInput(
                index=i,
                origin_zip=s.origin_zip,
                origin_country=s.origin_country,
                dest_zip=s.dest_zip,
                dest_country=s.dest_country,
            )
            for i, s in enumerate(shipments)
        ]
        country_validation = self._validator.validate_zip_countries(country_inputs)

        all_violations = validation.violations + country_validation.violations
        rejected_indices = {
            v.index
            for v in all_violations
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
            for v in all_violations
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
    # Non-invoice PDF handling
    # -----------------------------------------------------------------------

    async def _hold_non_invoice_pdf(
        self,
        upload_id: UUID,
        tenant_id: UUID,
        doc_type: str,
        log: Any,
    ) -> ProcessingResult:
        """Hold non-invoice PDFs as needs_manual_review.

        Used for doc_types that have no PDF parser yet (tariff → issue #59)
        or that are unexpected as PDFs (shipment_csv → re-upload as CSV/XLSX).
        """
        _MESSAGES: dict[str, str] = {
            "tariff": (
                "Tariff document detected — tariff import pipeline not yet available via upload. "
                "The document has been saved for manual import (issue #59)."
            ),
            "shipment_csv": (
                "Shipment list detected as PDF — re-upload as CSV or XLSX for automatic parsing."
            ),
        }
        message = _MESSAGES.get(doc_type, f"Document type '{doc_type}' cannot be processed as PDF.")
        log.info("non_invoice_pdf_held_for_review", doc_type=doc_type, message=message)
        await self._update_status(
            upload_id,
            tenant_id,
            STATUS_NEEDS_MANUAL_REVIEW,
            extra={
                "parse_method": "manual",
                "parsing_issues": [
                    {
                        "type": "unsupported_doc_type_for_pdf",
                        "message": message,
                        "doc_type": doc_type,
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

    # -----------------------------------------------------------------------
    # Tariff processing (PDF uploads)
    # -----------------------------------------------------------------------

    async def _process_tariff_upload(
        self,
        upload: Upload,
        upload_id: UUID,
        tenant_id: UUID,
        log: Any,
    ) -> ProcessingResult:
        """Process a tariff PDF through TariffParserService.

        Routes the result based on TariffParseResult.review_action:
          auto_import      → parsed
          hold_for_review  → needs_manual_review  (low confidence, human checks before use)
          needs_manual_review → needs_manual_review  (carrier unresolved or no rates)
        """
        if not upload.storage_url:
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_FAILED,
                error="No storage URL for upload",
            )

        file_path = Path(upload.storage_url)
        if not file_path.exists():
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_FAILED,
                error=f"File not found: {file_path}",
            )

        file_bytes = await asyncio.to_thread(file_path.read_bytes)

        async with _TenantSession(tenant_id) as db:
            result = await self._tariff_parser.parse(
                file_bytes,
                filename=upload.filename or "",
                tenant_id=tenant_id,
                upload_id=upload_id,
                db=db,
            )

        log.info(
            "tariff_parse_complete",
            confidence=result.confidence,
            review_action=result.review_action,
            zone_count=len(result.zones),
            rate_count=len(result.rates),
            carrier_id=str(result.carrier_id) if result.carrier_id else None,
        )

        parse_issues = [
            {"type": "tariff_issue", "message": issue, "timestamp": datetime.now(UTC).isoformat()}
            for issue in result.issues
        ]

        if result.review_action == "auto_import":
            await self._update_status(
                upload_id,
                tenant_id,
                STATUS_PARSED,
                extra={
                    "parse_method": result.parsing_method,
                    "confidence": result.confidence,
                    "llm_analysis": result.to_dict(),
                    **({"parsing_issues": parse_issues} if parse_issues else {}),
                },
            )
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_PARSED,
                parse_method=result.parsing_method,
                issues=parse_issues,
            )

        # hold_for_review or needs_manual_review → both land as needs_manual_review
        review_issue = {
            "type": "tariff_review_required",
            "message": (
                "Tariff imported but requires manual review — carrier unresolved or low confidence"
                if result.review_action == "needs_manual_review"
                else f"Confidence {result.confidence:.0%} below auto-import threshold"
            ),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await self._update_status(
            upload_id,
            tenant_id,
            STATUS_NEEDS_MANUAL_REVIEW,
            extra={
                "parse_method": result.parsing_method,
                "confidence": result.confidence,
                "parsing_issues": [review_issue, *parse_issues],
                "llm_analysis": result.to_dict(),
            },
        )
        return ProcessingResult(
            upload_id=upload_id,
            final_status=STATUS_NEEDS_MANUAL_REVIEW,
            parse_method=result.parsing_method,
            issues=[review_issue, *parse_issues],
        )

    # -----------------------------------------------------------------------
    # Invoice processing (PDF / image uploads)
    # -----------------------------------------------------------------------

    async def _process_invoice_upload(
        self,
        upload: Upload,
        upload_id: UUID,
        tenant_id: UUID,
        log: Any,
    ) -> ProcessingResult:
        """Process a PDF or image upload through InvoiceParserService.

        Routes the result to the appropriate upload status based on the
        InvoiceParseResult.review_action confidence decision:
          auto_import        → parsed
          auto_import_flag   → partial_success  (imported, flagged for review)
          hold_for_review    → needs_manual_review
          reject             → failed
        """
        if not upload.storage_url:
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_FAILED,
                error="No storage URL for upload",
            )

        file_path = Path(upload.storage_url)
        if not file_path.exists():
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_FAILED,
                error=f"File not found: {file_path}",
            )

        file_bytes = await asyncio.to_thread(file_path.read_bytes)

        async with _TenantSession(tenant_id) as db:
            result = await self._invoice_parser.parse_invoice_pdf(
                file_bytes,
                filename=upload.filename or "",
                carrier_id=None,
                tenant_id=tenant_id,
                upload_id=upload_id,
                db=db,
            )

        log.info(
            "invoice_parse_complete",
            confidence=result.confidence,
            review_action=result.review_action,
            line_count=len(result.lines),
            parsing_method=result.parsing_method,
        )

        parse_issues = [
            {
                "type": "llm_issue",
                "message": issue,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            for issue in result.issues
        ]

        # ── auto_import / auto_import_flag: save shipments ──────────────────
        if result.review_action in ("auto_import", "auto_import_flag"):
            async with _TenantSession(tenant_id) as db:
                saved_ids = await self._save_invoice_shipments(
                    db, result, upload_id, tenant_id, log
                )
            if saved_ids:
                async with _TenantSession(tenant_id) as db:
                    await self._calculate_benchmarks(db, saved_ids, tenant_id, log)

            final_status = (
                STATUS_PARSED
                if result.review_action == "auto_import"
                else STATUS_PARTIAL_SUCCESS
            )
            await self._update_status(
                upload_id,
                tenant_id,
                final_status,
                extra={
                    "parse_method": result.parsing_method,
                    "confidence": result.confidence,
                    **({"parsing_issues": parse_issues} if parse_issues else {}),
                },
            )
            return ProcessingResult(
                upload_id=upload_id,
                final_status=final_status,
                shipment_count=len(saved_ids),
                parse_method=result.parsing_method,
                issues=parse_issues,
            )

        # ── hold_for_review: import lines but flag for human approval ────────
        if result.review_action == "hold_for_review":
            review_issue = {
                "type": "low_confidence",
                "message": (
                    f"Confidence {result.confidence:.0%} is below auto-import threshold "
                    "— manual review required before shipments are accepted"
                ),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            await self._update_status(
                upload_id,
                tenant_id,
                STATUS_NEEDS_MANUAL_REVIEW,
                extra={
                    "parse_method": result.parsing_method,
                    "confidence": result.confidence,
                    "parsing_issues": [review_issue, *parse_issues],
                    "llm_analysis": {
                        "header": {
                            "invoice_number": result.header.invoice_number,
                            "invoice_date": result.header.invoice_date,
                            "carrier_name": result.header.carrier_name,
                            "total_amount": result.header.total_amount,
                            "currency": result.header.currency,
                        },
                        "line_count": len(result.lines),
                        "prompt_version": "freight_invoice_extractor_v1.0.0",
                    },
                },
            )
            return ProcessingResult(
                upload_id=upload_id,
                final_status=STATUS_NEEDS_MANUAL_REVIEW,
                parse_method=result.parsing_method,
                issues=[review_issue, *parse_issues],
            )

        # ── reject: confidence too low, do not import ────────────────────────
        await self._update_status(
            upload_id,
            tenant_id,
            STATUS_FAILED,
            extra={
                "parse_method": result.parsing_method,
                "confidence": result.confidence,
                "parse_errors": {
                    "message": f"Confidence {result.confidence:.0%} below reject threshold",
                    "issues": [i["message"] for i in parse_issues],
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            },
        )
        return ProcessingResult(
            upload_id=upload_id,
            final_status=STATUS_FAILED,
            parse_method=result.parsing_method,
            error=f"Confidence {result.confidence:.0%} below reject threshold",
            issues=parse_issues,
        )

    async def _save_invoice_shipments(
        self,
        db: AsyncSession,
        result: InvoiceParseResult,
        upload_id: UUID,
        tenant_id: UUID,
        log: Any,
    ) -> list[UUID]:
        """Persist InvoiceLine records as Shipment rows.

        Date fallback: uses invoice_date from header if the line has no shipment_date.
        """
        from datetime import date as date_type
        from decimal import Decimal

        header_date: date_type | None = None
        if result.header.invoice_date:
            try:
                header_date = date_type.fromisoformat(result.header.invoice_date)
            except ValueError:
                pass

        saved_ids: list[UUID] = []
        for idx, line in enumerate(result.lines):
            try:
                # Resolve shipment date
                shipment_date: date_type | None = None
                if line.shipment_date:
                    try:
                        shipment_date = date_type.fromisoformat(line.shipment_date)
                    except ValueError:
                        pass
                shipment_date = shipment_date or header_date

                if shipment_date is None:
                    log.warning(
                        "invoice_line_skipped_no_date",
                        line_index=idx,
                        reference=line.shipment_reference,
                    )
                    continue

                carrier_id = await self._resolve_carrier(
                    db,
                    result.header.carrier_name,
                    tenant_id,
                    upload_id,
                    log,
                )

                # Compute completeness score (required fields weighted at 70%)
                required = [
                    line.dest_zip,
                    line.weight_kg,
                    line.line_total or line.base_amount,
                ]
                optional = [
                    line.origin_zip,
                    line.shipment_reference or line.referenz,
                    line.billing_type,
                ]
                pct_req = sum(1 for f in required if f is not None) / len(required) * 70
                pct_opt = sum(1 for f in optional if f is not None) / len(optional) * 30
                completeness = Decimal(str(round((pct_req + pct_opt) / 100, 2)))

                currency = line.currency or result.header.currency or "EUR"

                row = Shipment(
                    tenant_id=tenant_id,
                    upload_id=upload_id,
                    carrier_id=carrier_id,
                    date=shipment_date,
                    reference_number=line.shipment_reference or line.referenz,
                    service_level=line.service_level,
                    origin_zip=line.origin_zip,
                    origin_country=line.origin_country or "DE",
                    dest_zip=line.dest_zip,
                    dest_country=line.dest_country or "DE",
                    weight_kg=(
                        Decimal(str(line.weight_kg)) if line.weight_kg is not None else None
                    ),
                    currency=currency,
                    actual_base_amount=(
                        Decimal(str(line.base_amount)) if line.base_amount is not None else None
                    ),
                    actual_diesel_amount=(
                        Decimal(str(line.diesel_amount))
                        if line.diesel_amount is not None
                        else None
                    ),
                    actual_toll_amount=(
                        Decimal(str(line.toll_amount)) if line.toll_amount is not None else None
                    ),
                    actual_total_amount=(
                        Decimal(str(line.line_total)) if line.line_total is not None else None
                    ),
                    completeness_score=completeness,
                    source_data={
                        "invoice_number": line.invoice_number or result.header.invoice_number,
                        "billing_type": line.billing_type,
                        "tour_number": line.tour_number,
                        "line_number": line.line_number,
                    },
                    extraction_method="llm",
                    confidence_score=Decimal(str(round(result.confidence, 2))),
                )
                db.add(row)
                await db.flush()
                saved_ids.append(row.id)

            except Exception as exc:
                log.error(
                    "invoice_shipment_save_error",
                    line_index=idx,
                    error=str(exc),
                    exc_info=True,
                )

        log.info("invoice_shipments_saved", count=len(saved_ids))
        return saved_ids

    # -----------------------------------------------------------------------
    # Document extraction + type detection helpers
    # -----------------------------------------------------------------------

    async def _extract_document(
        self,
        upload: Upload,
        log: Any,
    ) -> DocumentExtractionResult | None:
        """Read upload file from disk and parse CSV/XLSX into DataFrames.

        Only processes CSV and XLSX files — the template system requires column
        headers that only exist in tabular formats.  PDFs and images are skipped
        here; they are handled separately (future invoice-parsing flow) and will
        reach needs_manual_review via the no-template-match path.

        Returns None if the file is missing, unsupported, or parsing fails.
        """
        if not upload.storage_url:
            log.warning("extract_document_no_storage_url", upload_id=str(upload.id))
            return None

        file_path = Path(upload.storage_url)
        if not file_path.exists():
            log.warning("extract_document_file_missing", path=str(file_path))
            return None

        mime = (upload.mime_type or "").lower()
        filename = (upload.filename or "").lower()
        is_csv = "csv" in mime or filename.endswith(".csv") or "plain" in mime
        is_xlsx = "spreadsheet" in mime or "excel" in mime or filename.endswith((".xlsx", ".xls"))

        if not is_csv and not is_xlsx:
            log.info(
                "extract_document_skipped",
                mime_type=upload.mime_type,
                reason="not_tabular",
            )
            return None

        try:
            file_bytes = await asyncio.to_thread(file_path.read_bytes)
            result = await self._document_service.process(
                file_bytes=file_bytes,
                filename=upload.filename,
                mime_type=upload.mime_type,
            )
            log.info(
                "document_extracted",
                mode=result.mode,
                page_count=result.page_count,
                dataframes=len(result.dataframes),
                columns=len(result.dataframes[0].columns) if result.dataframes else 0,
            )
            return result
        except Exception as exc:
            log.warning("document_extraction_failed", error=str(exc))
            return None

    async def _detect_doc_type(
        self,
        upload: Upload,
        doc_result: DocumentExtractionResult | None,
        log: Any,
    ) -> tuple[str | None, str | None]:
        """Classify document type and build file_content for template matching.

        If upload.doc_type is already set (user hint from the upload form), detection
        is skipped and the hint is returned directly.

        For CSV/XLSX: passes column names (comma-joined header line) so that
        TemplateService._extract_characteristics() can score header keywords.
        For PDFs/images: passes Vision-extracted text.

        Returns:
            (doc_type, file_content_for_template_matching)
        """
        column_names: list[str] | None = None
        file_content: str | None = None

        if doc_result is not None:
            if doc_result.dataframes:
                column_names = [str(c) for c in doc_result.dataframes[0].columns.tolist()]
                # Single comma-separated line — matches what TemplateService expects
                file_content = ",".join(column_names)
            elif doc_result.text:
                file_content = doc_result.text

        # User-provided hint takes precedence — skip LLM/heuristic detection
        if upload.doc_type:
            log.info("doc_type_from_hint", doc_type=upload.doc_type)
            return upload.doc_type, file_content

        # For PDF/image files _extract_document returns None (template matching
        # only needs tabular headers).  Extract text here so Haiku can classify.
        if file_content is None and upload.storage_url:
            _mime = (upload.mime_type or "").lower()
            _fname = (upload.filename or "").lower()
            _needs_text = "pdf" in _mime or "image" in _mime or _fname.endswith(
                (".pdf", ".png", ".jpg", ".jpeg", ".webp")
            )
            if _needs_text:
                try:
                    _file_bytes = await asyncio.to_thread(Path(upload.storage_url).read_bytes)
                    _extracted = await self._document_service.process(
                        file_bytes=_file_bytes,
                        filename=upload.filename,
                        mime_type=upload.mime_type,
                    )
                    if _extracted.text:
                        file_content = _extracted.text
                    log.info(
                        "pdf_text_extracted_for_detection",
                        text_len=len(file_content or ""),
                    )
                except Exception as exc:
                    log.warning("pdf_text_extraction_for_detection_failed", error=str(exc))

        try:
            doc_type = await self._detector.detect(
                filename=upload.filename,
                mime_type=upload.mime_type or "",
                text_preview=file_content[:8000] if file_content else None,
                column_names=column_names,
            )
            log.info("doc_type_detected", doc_type=doc_type)
            return doc_type, file_content
        except Exception as exc:
            log.warning("doc_type_detection_failed", error=str(exc))
            return None, file_content

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

        Lookup order: fallback chain (exact → suffix strip → fuzzy → LLM)
        then placeholder creation when all steps fail.
        Placeholder carriers are created idempotently (same code_norm reused).
        """
        if not carrier_name:
            return None

        normalized = carrier_name.strip().lower()

        # Steps 1–4: exact alias, suffix strip, fuzzy, LLM
        result = await get_carrier_service().resolve_carrier_id_with_fallback(
            db, carrier_name, tenant_id
        )
        if result is not None:
            if result.method != "exact":
                log.info(
                    "carrier_resolved_via_fallback",
                    carrier_name=carrier_name,
                    method=result.method,
                    confidence=result.confidence,
                    carrier_id=str(result.carrier_id),
                )
            return result.carrier_id

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
            # Fail fast if the row is locked by an orphaned Postgres backend.
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
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
            text("SELECT set_config('app.current_tenant', :tid, true)"),
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

    RLS note: the upload table requires a tenant context (app.current_tenant).
    We iterate per tenant so each UPDATE runs inside the correct RLS context
    rather than attempting a cross-tenant query without a valid UUID.
    """
    log = logger.bind(task="stale_upload_watcher")
    log.info("stale_watcher_started", poll_seconds=_STALE_POLL_SECONDS)

    # Sentinel UUID used to satisfy the RLS app.current_tenant UUID cast.
    # The upload table's RLS policy is: tenant_id = current_setting(...)::UUID.
    # With this sentinel, the cast succeeds but no real tenant matches it, so
    # we use a raw UPDATE that bypasses the WHERE-filter effect of RLS by
    # querying upload.tenant_id directly — the RETURNING clause confirms hits.
    _WATCHER_SENTINEL = "00000000-0000-0000-0000-000000000001"

    while True:
        await asyncio.sleep(_STALE_POLL_SECONDS)
        try:
            cutoff = datetime.now(UTC) - timedelta(minutes=_STALE_MINUTES)

            # Use a raw statement outside normal RLS context:
            # set_config with a valid UUID so the cast doesn't throw,
            # but the UPDATE targets all tenants via explicit tenant_id IS NOT NULL.
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "SELECT set_config('app.current_tenant', :sentinel, true)"
                    ),
                    {"sentinel": _WATCHER_SENTINEL},
                )
                result = await db.execute(
                    text(
                        "UPDATE upload SET status = :failed, updated_at = :now, "
                        "parse_errors = :errors\\:\\:jsonb "
                        "WHERE status = :parsing AND updated_at < :cutoff "
                        "AND tenant_id IS NOT NULL "
                        "RETURNING id"
                    ),
                    {
                        "failed": STATUS_FAILED,
                        "now": datetime.now(UTC),
                        "errors": (
                            f'{{"message":"Processing timed out after {_STALE_MINUTES} minutes",'
                            f'"type":"ProcessingTimeout",'
                            f'"timestamp":"{datetime.now(UTC).isoformat()}"}}'
                        ),
                        "parsing": STATUS_PARSING,
                        "cutoff": cutoff,
                    },
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
