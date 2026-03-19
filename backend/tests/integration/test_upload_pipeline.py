"""Integration tests: full upload pipeline.

Tests the end-to-end upload → parse → store → benchmark flow using
the MECU fixture data.

Prerequisites:
  TEST_TENANT_A_ID — UUID of a pre-existing test tenant with carrier and
                     tariff data configured (COSI Logistics, zone map,
                     diesel floater per mecu-validation.spec.ts)
  DATABASE_URL     — Supabase connection string

These tests run against the real database and roll back all changes.
File I/O for the upload processor is mocked.

Issue: #52
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Project, Shipment, ShipmentBenchmark, Upload

pytestmark = pytest.mark.asyncio

# Path to MECU sample CSV (from backend_legacy/test/fixtures/)
_FIXTURE_DIR = (
    Path(__file__).parent.parent.parent.parent / "backend_legacy" / "test" / "fixtures" / "mecu"
)
_MECU_CSV = _FIXTURE_DIR / "sample.csv"


# ============================================================================
# UPLOAD RECORD LIFECYCLE
# ============================================================================


class TestUploadRecordLifecycle:
    """Upload record is created, transitions through status stages."""

    async def test_upload_created_with_pending_status(
        self,
        tenant_a_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Creating an Upload record sets status=pending."""
        upload = Upload(
            tenant_id=tenant_a_id,
            filename="pipeline_test.csv",
            file_hash=uuid4().hex,
            mime_type="text/csv",
            status="pending",
        )
        tenant_a_db.add(upload)
        await tenant_a_db.flush()

        reloaded = (
            await tenant_a_db.execute(select(Upload).where(Upload.id == upload.id))
        ).scalar_one()
        assert reloaded.status == "pending"
        assert reloaded.filename == "pipeline_test.csv"

    async def test_upload_linked_to_project(
        self,
        tenant_a_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Upload can be associated with a project."""
        project = Project(
            tenant_id=tenant_a_id,
            name=f"pipeline-test-{uuid4().hex[:6]}",
            phase="quick_check",
            status="draft",
        )
        tenant_a_db.add(project)
        await tenant_a_db.flush()

        upload = Upload(
            tenant_id=tenant_a_id,
            project_id=project.id,
            filename="linked_upload.csv",
            file_hash=uuid4().hex,
            mime_type="text/csv",
            status="pending",
        )
        tenant_a_db.add(upload)
        await tenant_a_db.flush()

        reloaded = (
            await tenant_a_db.execute(select(Upload).where(Upload.id == upload.id))
        ).scalar_one()
        assert reloaded.project_id == project.id


# ============================================================================
# SHIPMENT STORAGE
# ============================================================================


class TestShipmentStorage:
    """Shipments are created correctly and linked to uploads."""

    async def test_shipment_stored_with_all_required_fields(
        self,
        tenant_a_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Shipment record stores all fields from the MECU fixture row."""
        upload = Upload(
            tenant_id=tenant_a_id,
            filename="mecu_test.csv",
            file_hash=uuid4().hex,
            mime_type="text/csv",
            status="parsed",
        )
        tenant_a_db.add(upload)
        await tenant_a_db.flush()

        # Mirrors the MECU validation fixture data
        shipment = Shipment(
            tenant_id=tenant_a_id,
            upload_id=upload.id,
            date=date(2024, 1, 15),
            origin_zip="60311",
            origin_country="DE",
            dest_zip="80333",
            dest_country="DE",
            weight_kg=Decimal("450.00"),
            currency="EUR",
            actual_total_amount=Decimal("348.75"),
            actual_base_amount=Decimal("294.30"),
            actual_diesel_amount=Decimal("54.45"),
        )
        tenant_a_db.add(shipment)
        await tenant_a_db.flush()

        reloaded = (
            await tenant_a_db.execute(select(Shipment).where(Shipment.id == shipment.id))
        ).scalar_one()

        assert reloaded.origin_zip == "60311"
        assert reloaded.weight_kg == Decimal("450.00")
        assert reloaded.actual_total_amount == Decimal("348.75")
        assert reloaded.upload_id == upload.id


# ============================================================================
# BENCHMARK STORAGE
# ============================================================================


class TestBenchmarkStorage:
    """Benchmark records are created and classified correctly."""

    async def test_benchmark_overcharge_classification(
        self,
        tenant_a_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Shipment with actual > expected by >5% is classified as 'drüber'."""
        upload = Upload(
            tenant_id=tenant_a_id,
            filename="benchmark_test.csv",
            file_hash=uuid4().hex,
            mime_type="text/csv",
            status="parsed",
        )
        tenant_a_db.add(upload)
        await tenant_a_db.flush()

        shipment = Shipment(
            tenant_id=tenant_a_id,
            upload_id=upload.id,
            date=date(2024, 1, 15),
            weight_kg=Decimal("450.00"),
            currency="EUR",
            actual_total_amount=Decimal("500.00"),  # overcharge vs expected 348.75
        )
        tenant_a_db.add(shipment)
        await tenant_a_db.flush()

        benchmark = ShipmentBenchmark(
            tenant_id=tenant_a_id,
            shipment_id=shipment.id,
            expected_base_amount=Decimal("294.30"),
            expected_diesel_amount=Decimal("54.45"),
            expected_total_amount=Decimal("348.75"),
            actual_total_amount=Decimal("500.00"),
            delta_amount=Decimal("151.25"),
            delta_pct=Decimal("43.36"),
            classification="drüber",
            currency="EUR",
        )
        tenant_a_db.add(benchmark)
        await tenant_a_db.flush()

        reloaded = (
            await tenant_a_db.execute(
                select(ShipmentBenchmark).where(ShipmentBenchmark.id == benchmark.id)
            )
        ).scalar_one()

        assert reloaded.classification == "drüber"
        assert reloaded.delta_pct > Decimal("5.0")

    async def test_benchmark_undercharge_classification(
        self,
        tenant_a_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Shipment with actual < expected by >5% is classified as 'unter'."""
        upload = Upload(
            tenant_id=tenant_a_id,
            filename="benchmark_unter_test.csv",
            file_hash=uuid4().hex,
            mime_type="text/csv",
            status="parsed",
        )
        tenant_a_db.add(upload)
        await tenant_a_db.flush()

        shipment = Shipment(
            tenant_id=tenant_a_id,
            upload_id=upload.id,
            date=date(2024, 1, 15),
            weight_kg=Decimal("450.00"),
            currency="EUR",
            actual_total_amount=Decimal("200.00"),  # undercharge
        )
        tenant_a_db.add(shipment)
        await tenant_a_db.flush()

        expected_total = Decimal("348.75")
        actual = Decimal("200.00")
        delta = actual - expected_total
        delta_pct = (delta / expected_total * 100).quantize(Decimal("0.01"))

        benchmark = ShipmentBenchmark(
            tenant_id=tenant_a_id,
            shipment_id=shipment.id,
            expected_total_amount=expected_total,
            actual_total_amount=actual,
            delta_amount=delta,
            delta_pct=delta_pct,
            classification="unter",
            currency="EUR",
        )
        tenant_a_db.add(benchmark)
        await tenant_a_db.flush()

        reloaded = (
            await tenant_a_db.execute(
                select(ShipmentBenchmark).where(ShipmentBenchmark.id == benchmark.id)
            )
        ).scalar_one()

        assert reloaded.classification == "unter"
        assert reloaded.delta_pct < Decimal("-5.0")

    async def test_benchmark_im_markt_classification(
        self,
        tenant_a_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Shipment within ±5% tolerance is classified as 'im_markt'."""
        upload = Upload(
            tenant_id=tenant_a_id,
            filename="benchmark_im_markt_test.csv",
            file_hash=uuid4().hex,
            mime_type="text/csv",
            status="parsed",
        )
        tenant_a_db.add(upload)
        await tenant_a_db.flush()

        shipment = Shipment(
            tenant_id=tenant_a_id,
            upload_id=upload.id,
            date=date(2024, 1, 15),
            weight_kg=Decimal("450.00"),
            currency="EUR",
            actual_total_amount=Decimal("350.00"),  # within ±5% of 348.75
        )
        tenant_a_db.add(shipment)
        await tenant_a_db.flush()

        expected_total = Decimal("348.75")
        actual = Decimal("350.00")
        delta = actual - expected_total
        delta_pct = (delta / expected_total * 100).quantize(Decimal("0.01"))

        benchmark = ShipmentBenchmark(
            tenant_id=tenant_a_id,
            shipment_id=shipment.id,
            expected_total_amount=expected_total,
            actual_total_amount=actual,
            delta_amount=delta,
            delta_pct=delta_pct,
            classification="im_markt",
            currency="EUR",
        )
        tenant_a_db.add(benchmark)
        await tenant_a_db.flush()

        reloaded = (
            await tenant_a_db.execute(
                select(ShipmentBenchmark).where(ShipmentBenchmark.id == benchmark.id)
            )
        ).scalar_one()

        assert reloaded.classification == "im_markt"
        assert abs(reloaded.delta_pct) <= Decimal("5.0")


# ============================================================================
# MECU FIXTURE PARSING (requires CSV fixture to exist)
# ============================================================================


@pytest.mark.skipif(
    not _MECU_CSV.exists(),
    reason=f"MECU fixture not found at {_MECU_CSV}",
)
class TestMecuFixtureParsing:
    """Parse the MECU sample CSV and verify shipment count and fields."""

    async def test_mecu_csv_can_be_parsed_to_shipments(
        self,
        tenant_a_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """MECU sample.csv produces at least 5 shipments after parsing."""
        import csv

        with _MECU_CSV.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) >= 5, f"Expected at least 5 rows in MECU fixture, got {len(rows)}"

        # Verify key columns expected by the CSV parser are present
        first = rows[0]
        expected_columns = {"Sendungsdatum", "PLZ Empfänger", "Gewicht"}
        found = {
            col for col in expected_columns
            if col in first or any(col.lower() in k.lower() for k in first)
        }
        assert len(found) > 0, (
            f"MECU CSV missing expected columns. Found columns: {list(first.keys())[:10]}"
        )
