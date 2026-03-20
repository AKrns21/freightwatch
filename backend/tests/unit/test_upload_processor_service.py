"""Unit tests for UploadProcessorService.

Tests: pipeline orchestration, status transitions, carrier resolution,
       concurrency semaphore, stale-watcher logic.

No real DB — all SQLAlchemy sessions are replaced with AsyncMock.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.extraction_validator_service import (
    ExtractionValidationResult,
    ValidationViolation,
)
from app.services.parsing.csv_parser import ParsedShipment, RowParseError
from app.services.template_service import TemplateMatch
from app.services.upload_processor_service import (
    STATUS_FAILED,
    STATUS_NEEDS_MANUAL_REVIEW,
    STATUS_PARSED,
    STATUS_PARTIAL_SUCCESS,
    STATUS_PARSING,
    ProcessingResult,
    UploadProcessorService,
    _get_semaphore,
    get_upload_processor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_ID = uuid4()
UPLOAD_ID = uuid4()

_MODULE = "app.services.upload_processor_service"


def _make_upload(
    upload_id: UUID = UPLOAD_ID,
    tenant_id: UUID = TENANT_ID,
    status: str = "pending",
    storage_url: str = "/tmp/test.csv",
    mime_type: str = "text/csv",
    filename: str = "test.csv",
) -> MagicMock:
    upload = MagicMock()
    upload.id = upload_id
    upload.tenant_id = tenant_id
    upload.status = status
    upload.storage_url = storage_url
    upload.mime_type = mime_type
    upload.filename = filename
    upload.parsing_issues = None
    return upload


def _make_template_match(confidence: float = 0.9) -> TemplateMatch:
    template = MagicMock()
    template.id = uuid4()
    template.name = "Test Template"
    template.mappings = {"date": "Datum", "carrier_name": "Spediteur"}
    return TemplateMatch(template=template, confidence=confidence, reasons=["mime_match"])


def _make_parsed_shipment(ref: str | None = "REF001") -> ParsedShipment:
    return ParsedShipment(
        tenant_id=str(TENANT_ID),
        upload_id=str(UPLOAD_ID),
        date=datetime(2026, 1, 15).date(),
        carrier_name="DHL Freight",
        origin_zip="10115",
        dest_zip="80331",
        weight_kg=Decimal("100.0"),
        actual_total_amount=Decimal("150.00"),
        currency="EUR",
        reference_number=ref,
        completeness_score=Decimal("0.95"),
    )


def _mock_tenant_session() -> MagicMock:
    """Return a MagicMock that acts as an async context manager yielding an AsyncMock db."""
    db = AsyncMock()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


class TestUploadProcessorService:
    """Test suite for UploadProcessorService."""

    def setup_method(self) -> None:
        self.svc = UploadProcessorService()

    # ============================================================================
    # UPLOAD NOT FOUND
    # ============================================================================

    @pytest.mark.asyncio
    async def test_upload_not_found_returns_failed(self) -> None:
        with patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()), \
             patch.object(self.svc, "_load_upload", return_value=None):

            result = await self.svc._run_pipeline(UPLOAD_ID, TENANT_ID)

        assert result.final_status == STATUS_FAILED
        assert "not found" in (result.error or "")

    # ============================================================================
    # TEMPLATE NOT FOUND → NEEDS_MANUAL_REVIEW
    # ============================================================================

    @pytest.mark.asyncio
    async def test_no_template_match_sets_needs_manual_review(self) -> None:
        upload = _make_upload()

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc._template_service, "find_match", new_callable=AsyncMock, return_value=None),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock) as mock_update,
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        assert result.final_status == STATUS_NEEDS_MANUAL_REVIEW
        # First call sets 'parsing'; second call sets 'needs_manual_review'
        statuses = [call.args[2] for call in mock_update.call_args_list]
        assert STATUS_PARSING in statuses
        assert STATUS_NEEDS_MANUAL_REVIEW in statuses

    # ============================================================================
    # HAPPY PATH → PARSED
    # ============================================================================

    @pytest.mark.asyncio
    async def test_successful_parse_sets_parsed_status(self) -> None:
        upload = _make_upload()
        match = _make_template_match(confidence=0.9)
        shipment = _make_parsed_shipment()
        saved_id = uuid4()

        validation_result = ExtractionValidationResult(status="pass", violations=[])

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc._template_service, "find_match", new_callable=AsyncMock, return_value=match),
            patch.object(self.svc, "_parse", new_callable=AsyncMock, return_value=([shipment], [], 0.95)),
            patch.object(self.svc, "_fetch_existing_refs", new_callable=AsyncMock, return_value=set()),
            patch.object(self.svc._validator, "validate_shipments", return_value=validation_result),
            patch.object(self.svc, "_save_shipments", new_callable=AsyncMock, return_value=[saved_id]),
            patch.object(self.svc, "_calculate_benchmarks", new_callable=AsyncMock),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock) as mock_update,
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        assert result.final_status == STATUS_PARSED
        assert result.shipment_count == 1
        assert result.row_error_count == 0
        final_status_call = mock_update.call_args_list[-1]
        assert final_status_call.args[2] == STATUS_PARSED

    # ============================================================================
    # ROW ERRORS → PARTIAL_SUCCESS
    # ============================================================================

    @pytest.mark.asyncio
    async def test_row_errors_with_some_shipments_sets_partial_success(self) -> None:
        upload = _make_upload()
        match = _make_template_match(confidence=0.85)
        shipment = _make_parsed_shipment()
        error = RowParseError(row=3, error="missing date", raw_data=None)

        validation_result = ExtractionValidationResult(status="pass", violations=[])

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc._template_service, "find_match", new_callable=AsyncMock, return_value=match),
            patch.object(self.svc, "_parse", new_callable=AsyncMock, return_value=([shipment], [error], 0.7)),
            patch.object(self.svc, "_fetch_existing_refs", new_callable=AsyncMock, return_value=set()),
            patch.object(self.svc._validator, "validate_shipments", return_value=validation_result),
            patch.object(self.svc, "_save_shipments", new_callable=AsyncMock, return_value=[uuid4()]),
            patch.object(self.svc, "_calculate_benchmarks", new_callable=AsyncMock),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        assert result.final_status == STATUS_PARTIAL_SUCCESS
        assert result.row_error_count == 1

    # ============================================================================
    # ALL ROWS FAIL → FAILED
    # ============================================================================

    @pytest.mark.asyncio
    async def test_all_rows_fail_sets_failed(self) -> None:
        upload = _make_upload()
        match = _make_template_match()
        error = RowParseError(row=1, error="bad row", raw_data=None)

        validation_result = ExtractionValidationResult(status="pass", violations=[])

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc._template_service, "find_match", new_callable=AsyncMock, return_value=match),
            patch.object(self.svc, "_parse", new_callable=AsyncMock, return_value=([], [error], 0.0)),
            patch.object(self.svc, "_fetch_existing_refs", new_callable=AsyncMock, return_value=set()),
            patch.object(self.svc._validator, "validate_shipments", return_value=validation_result),
            patch.object(self.svc, "_save_shipments", new_callable=AsyncMock, return_value=[]),
            patch.object(self.svc, "_calculate_benchmarks", new_callable=AsyncMock),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        assert result.final_status == STATUS_FAILED
        assert result.shipment_count == 0

    # ============================================================================
    # VALIDATION REJECT — duplicate reference dropped
    # ============================================================================

    @pytest.mark.asyncio
    async def test_duplicate_reference_is_rejected(self) -> None:
        upload = _make_upload()
        match = _make_template_match()
        shipment = _make_parsed_shipment(ref="EXISTING_REF")

        dup_violation = ValidationViolation(
            entity="shipment",
            rule="reference_number_dedup",
            action="reject",
            detail="already exists",
            index=0,
        )
        validation_result = ExtractionValidationResult(
            status="fail", violations=[dup_violation]
        )

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc._template_service, "find_match", new_callable=AsyncMock, return_value=match),
            patch.object(self.svc, "_parse", new_callable=AsyncMock, return_value=([shipment], [], 1.0)),
            patch.object(self.svc, "_fetch_existing_refs", new_callable=AsyncMock, return_value={"EXISTING_REF"}),
            patch.object(self.svc._validator, "validate_shipments", return_value=validation_result),
            patch.object(self.svc, "_save_shipments", new_callable=AsyncMock, return_value=[]),
            patch.object(self.svc, "_calculate_benchmarks", new_callable=AsyncMock),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        # One shipment rejected, no valid saved → FAILED
        assert result.final_status == STATUS_FAILED
        assert result.shipment_count == 0

    # ============================================================================
    # EXCEPTION → ERROR status
    # ============================================================================

    @pytest.mark.asyncio
    async def test_unhandled_exception_sets_failed_status(self) -> None:
        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=_make_upload()),
            patch.object(
                self.svc,
                "_pipeline_stages",
                side_effect=RuntimeError("unexpected boom"),
            ),
            patch.object(self.svc, "_set_status_error", new_callable=AsyncMock) as mock_err,
        ):
            result = await self.svc._run_pipeline(UPLOAD_ID, TENANT_ID)

        assert result.final_status == STATUS_FAILED
        assert "unexpected boom" in (result.error or "")
        mock_err.assert_called_once()

    # ============================================================================
    # PARSE — unsupported mime type
    # ============================================================================

    @pytest.mark.asyncio
    async def test_parse_unsupported_mime_returns_empty(self) -> None:
        upload = _make_upload(mime_type="application/pdf", filename="invoice.pdf")
        match = _make_template_match()

        shipments, errors, confidence = await self.svc._parse(upload, match)

        assert shipments == []
        assert errors == []
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_parse_none_storage_url_returns_empty(self) -> None:
        upload = _make_upload(storage_url=None)
        match = _make_template_match()

        shipments, errors, confidence = await self.svc._parse(upload, match)

        assert shipments == []

    # ============================================================================
    # PROCESSING RESULT to_dict
    # ============================================================================

    def test_processing_result_to_dict(self) -> None:
        result = ProcessingResult(
            upload_id=UPLOAD_ID,
            final_status=STATUS_PARSED,
            shipment_count=42,
            row_error_count=3,
            parse_method="template",
        )
        d = result.to_dict()

        assert d["upload_id"] == str(UPLOAD_ID)
        assert d["final_status"] == STATUS_PARSED
        assert d["shipment_count"] == 42
        assert d["row_error_count"] == 3
        assert d["parse_method"] == "template"

    # ============================================================================
    # SEMAPHORE
    # ============================================================================

    def test_get_semaphore_returns_same_instance(self) -> None:
        s1 = _get_semaphore()
        s2 = _get_semaphore()
        assert s1 is s2

    @pytest.mark.asyncio
    async def test_process_upload_acquires_semaphore(self) -> None:
        """Verify process_upload runs through the semaphore (smoke test)."""
        with patch.object(
            self.svc,
            "_run_pipeline",
            new_callable=AsyncMock,
            return_value=ProcessingResult(upload_id=UPLOAD_ID, final_status=STATUS_PARSED),
        ) as mock_run:
            result = await self.svc.process_upload(UPLOAD_ID, TENANT_ID)

        mock_run.assert_called_once_with(UPLOAD_ID, TENANT_ID)
        assert result.final_status == STATUS_PARSED

    # ============================================================================
    # SINGLETON
    # ============================================================================

    def test_get_upload_processor_returns_singleton(self) -> None:
        import app.services.upload_processor_service as mod

        mod._upload_processor = None  # reset
        p1 = get_upload_processor()
        p2 = get_upload_processor()
        assert p1 is p2

    # ============================================================================
    # NON-INVOICE PDF ROUTING — tariff / shipment_csv PDFs
    # ============================================================================

    @pytest.mark.asyncio
    async def test_tariff_pdf_does_not_enter_invoice_parser(self) -> None:
        """A PDF detected as 'tariff' must never call InvoiceParserService."""
        upload = _make_upload(mime_type="application/pdf", filename="AS 04.2022 Dirk Beese.pdf")
        tariff_result = ProcessingResult(upload_id=UPLOAD_ID, final_status=STATUS_NEEDS_MANUAL_REVIEW)

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc, "_extract_document", new_callable=AsyncMock, return_value=None),
            patch.object(self.svc, "_detect_doc_type", new_callable=AsyncMock, return_value=("tariff", None)),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
            patch.object(self.svc, "_process_invoice_upload", new_callable=AsyncMock) as mock_invoice,
            patch.object(self.svc, "_process_tariff_upload", new_callable=AsyncMock, return_value=tariff_result),
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        mock_invoice.assert_not_called()
        assert result.final_status == STATUS_NEEDS_MANUAL_REVIEW

    @pytest.mark.asyncio
    async def test_tariff_pdf_routes_to_tariff_parser(self) -> None:
        """A PDF detected as 'tariff' must call _process_tariff_upload."""
        upload = _make_upload(mime_type="application/pdf", filename="tariff_2022.pdf")
        tariff_result = ProcessingResult(
            upload_id=UPLOAD_ID, final_status=STATUS_PARSED, parse_method="llm"
        )

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc, "_extract_document", new_callable=AsyncMock, return_value=None),
            patch.object(self.svc, "_detect_doc_type", new_callable=AsyncMock, return_value=("tariff", None)),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
            patch.object(
                self.svc, "_process_tariff_upload", new_callable=AsyncMock, return_value=tariff_result
            ) as mock_tariff,
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        mock_tariff.assert_called_once()
        assert result.final_status == STATUS_PARSED

    @pytest.mark.asyncio
    async def test_shipment_csv_pdf_sets_needs_manual_review(self) -> None:
        """A PDF misclassified as shipment_csv should be held, not crashed."""
        upload = _make_upload(mime_type="application/pdf", filename="sendungsliste.pdf")

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc, "_extract_document", new_callable=AsyncMock, return_value=None),
            patch.object(self.svc, "_detect_doc_type", new_callable=AsyncMock, return_value=("shipment_csv", None)),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
            patch.object(self.svc, "_process_invoice_upload", new_callable=AsyncMock) as mock_invoice,
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        mock_invoice.assert_not_called()
        assert result.final_status == STATUS_NEEDS_MANUAL_REVIEW

    @pytest.mark.asyncio
    async def test_invoice_pdf_still_routes_to_invoice_parser(self) -> None:
        """Regression: 'invoice' PDFs must still enter InvoiceParserService."""
        upload = _make_upload(mime_type="application/pdf", filename="rechnung_2024.pdf")
        invoice_result = MagicMock()
        invoice_result.final_status = STATUS_PARSED

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc, "_extract_document", new_callable=AsyncMock, return_value=None),
            patch.object(self.svc, "_detect_doc_type", new_callable=AsyncMock, return_value=("invoice", None)),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
            patch.object(
                self.svc, "_process_invoice_upload", new_callable=AsyncMock, return_value=invoice_result
            ) as mock_invoice,
        ):
            await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        mock_invoice.assert_called_once()

    # ============================================================================
    # VALIDATE SHIPMENTS — partial reject produces issues list
    # ============================================================================

    @pytest.mark.asyncio
    async def test_validation_issues_included_in_result(self) -> None:
        upload = _make_upload()
        match = _make_template_match()
        s1 = _make_parsed_shipment(ref="NEW_REF")
        s2 = _make_parsed_shipment(ref="DUP_REF")

        dup_violation = ValidationViolation(
            entity="shipment",
            rule="reference_number_dedup",
            action="reject",
            detail="already exists",
            index=1,
        )
        validation_result = ExtractionValidationResult(
            status="fail", violations=[dup_violation]
        )

        with (
            patch(f"{_MODULE}._TenantSession", return_value=_mock_tenant_session()),
            patch.object(self.svc, "_load_upload", return_value=upload),
            patch.object(self.svc._template_service, "find_match", new_callable=AsyncMock, return_value=match),
            patch.object(self.svc, "_parse", new_callable=AsyncMock, return_value=([s1, s2], [], 1.0)),
            patch.object(self.svc, "_fetch_existing_refs", new_callable=AsyncMock, return_value={"DUP_REF"}),
            patch.object(self.svc._validator, "validate_shipments", return_value=validation_result),
            # Only s1 saved (s2 rejected)
            patch.object(self.svc, "_save_shipments", new_callable=AsyncMock, return_value=[uuid4()]),
            patch.object(self.svc, "_calculate_benchmarks", new_callable=AsyncMock),
            patch.object(self.svc, "_update_status", new_callable=AsyncMock),
        ):
            result = await self.svc._pipeline_stages(UPLOAD_ID, TENANT_ID, MagicMock())

        assert result.shipment_count == 1
        assert any(i["type"] == "validation_error" for i in result.issues)
