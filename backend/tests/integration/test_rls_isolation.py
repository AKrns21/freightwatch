"""Integration tests: multi-tenant RLS isolation.

Verifies that tenant A cannot see tenant B's data and vice versa
for all core tenant-scoped tables.

Prerequisites (set via environment variables):
  TEST_TENANT_A_ID  — UUID of a pre-existing test tenant
  TEST_TENANT_B_ID  — UUID of a different pre-existing test tenant

Each test is fully rolled back after execution — no data is persisted.

Issue: #52
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import FxRate, Project, Shipment, Upload

pytestmark = pytest.mark.asyncio


# ============================================================================
# PROJECTS
# ============================================================================


class TestProjectRlsIsolation:
    """Tenant A creates a project → tenant B cannot see it."""

    async def test_project_invisible_to_other_tenant(
        self,
        tenant_a_db: AsyncSession,
        tenant_b_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Project created by tenant A is not visible to tenant B."""
        project = Project(
            tenant_id=tenant_a_id,
            name=f"RLS-test-project-{uuid4().hex[:8]}",
            phase="quick_check",
            status="draft",
        )
        tenant_a_db.add(project)
        await tenant_a_db.flush()
        project_id = project.id

        # Tenant A can see it
        found_a = (
            await tenant_a_db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        assert found_a is not None, "Tenant A must be able to read its own project"

        # Tenant B cannot see it
        found_b = (
            await tenant_b_db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        assert found_b is None, "Tenant B must NOT see tenant A's project"

    async def test_cross_tenant_list_returns_empty(
        self,
        tenant_a_db: AsyncSession,
        tenant_b_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Global project list is scoped to current tenant only."""
        # Create two projects as tenant A
        for i in range(2):
            project = Project(
                tenant_id=tenant_a_id,
                name=f"RLS-list-test-{i}-{uuid4().hex[:6]}",
                phase="quick_check",
                status="draft",
            )
            tenant_a_db.add(project)
        await tenant_a_db.flush()

        # Tenant B list should not contain any of tenant A's projects
        rows_b = (
            await tenant_b_db.execute(select(Project).where(Project.deleted_at.is_(None)))
        ).scalars().all()
        tenant_a_ids = {str(tenant_a_id)}
        leaked = [r for r in rows_b if str(r.tenant_id) in tenant_a_ids]
        assert len(leaked) == 0, f"Cross-tenant project leakage detected: {leaked}"


# ============================================================================
# UPLOADS
# ============================================================================


class TestUploadRlsIsolation:
    """Tenant A creates an upload → tenant B cannot see it."""

    async def test_upload_invisible_to_other_tenant(
        self,
        tenant_a_db: AsyncSession,
        tenant_b_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        upload = Upload(
            tenant_id=tenant_a_id,
            filename=f"test_upload_{uuid4().hex[:8]}.csv",
            file_hash=uuid4().hex,
            mime_type="text/csv",
            status="pending",
        )
        tenant_a_db.add(upload)
        await tenant_a_db.flush()
        upload_id = upload.id

        # Tenant A sees it
        assert (
            await tenant_a_db.execute(select(Upload).where(Upload.id == upload_id))
        ).scalar_one_or_none() is not None

        # Tenant B does not
        assert (
            await tenant_b_db.execute(select(Upload).where(Upload.id == upload_id))
        ).scalar_one_or_none() is None, "Tenant B must NOT see tenant A's upload"


# ============================================================================
# SHIPMENTS
# ============================================================================


class TestShipmentRlsIsolation:
    """Tenant A creates a shipment → tenant B cannot see it."""

    async def test_shipment_invisible_to_other_tenant(
        self,
        tenant_a_db: AsyncSession,
        tenant_b_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        shipment = Shipment(
            tenant_id=tenant_a_id,
            date=date.today(),
            origin_zip="60311",
            origin_country="DE",
            dest_zip="80333",
            dest_country="DE",
            weight_kg=Decimal("450.00"),
            currency="EUR",
            actual_total_amount=Decimal("348.75"),
        )
        tenant_a_db.add(shipment)
        await tenant_a_db.flush()
        shipment_id = shipment.id

        # Tenant A sees it
        assert (
            await tenant_a_db.execute(select(Shipment).where(Shipment.id == shipment_id))
        ).scalar_one_or_none() is not None

        # Tenant B does not
        assert (
            await tenant_b_db.execute(select(Shipment).where(Shipment.id == shipment_id))
        ).scalar_one_or_none() is None, "Tenant B must NOT see tenant A's shipment"

    async def test_cross_tenant_query_returns_empty_not_error(
        self,
        tenant_a_db: AsyncSession,
        tenant_b_db: AsyncSession,
        tenant_a_id: str,
    ) -> None:
        """Cross-tenant queries return empty results — NOT a DB error."""
        # Create a shipment as tenant A
        shipment = Shipment(
            tenant_id=tenant_a_id,
            date=date.today(),
            weight_kg=Decimal("100.00"),
            currency="EUR",
        )
        tenant_a_db.add(shipment)
        await tenant_a_db.flush()

        # Tenant B queries the same table — must silently return nothing
        try:
            result = (
                await tenant_b_db.execute(
                    select(Shipment).where(Shipment.id == shipment.id)
                )
            ).scalar_one_or_none()
            assert result is None, "Expected None, not the shipment"
        except Exception as exc:
            pytest.fail(f"Cross-tenant query raised an exception: {exc}")


# ============================================================================
# FX RATES (no RLS — global reference table)
# ============================================================================


class TestFxRateGlobalVisibility:
    """FX rates have no RLS — both tenants see the same data."""

    async def test_fx_rate_visible_to_all_tenants(
        self,
        tenant_a_db: AsyncSession,
        tenant_b_db: AsyncSession,
    ) -> None:
        """FxRate rows are global reference data — accessible by all tenants."""
        from_ccy = "TEST"
        to_ccy = "EUR"
        rate_date = date(2000, 1, 1)  # Use old date to avoid conflicts

        # Insert via tenant A
        fx = FxRate(
            rate_date=rate_date,
            from_ccy=from_ccy,
            to_ccy=to_ccy,
            rate=Decimal("1.234"),
            source="rls_test",
        )
        tenant_a_db.add(fx)
        try:
            await tenant_a_db.flush()
        except Exception:
            # Row may already exist from a previous (non-rolled-back) run
            await tenant_a_db.rollback()
            pytest.skip("FxRate test row already exists — possible prior non-rollback")
            return

        # Both tenants can read it
        found_a = (
            await tenant_a_db.execute(
                select(FxRate).where(
                    FxRate.rate_date == rate_date,
                    FxRate.from_ccy == from_ccy,
                    FxRate.to_ccy == to_ccy,
                )
            )
        ).scalar_one_or_none()
        assert found_a is not None, "Tenant A must see FxRate"

        found_b = (
            await tenant_b_db.execute(
                select(FxRate).where(
                    FxRate.rate_date == rate_date,
                    FxRate.from_ccy == from_ccy,
                    FxRate.to_ccy == to_ccy,
                )
            )
        ).scalar_one_or_none()
        assert found_b is not None, "Tenant B must also see FxRate (no RLS)"


# ============================================================================
# SET LOCAL scope — context resets at transaction end
# ============================================================================


class TestSetLocalScope:
    """Verify that SET LOCAL app.current_tenant resets at transaction end."""

    async def test_tenant_context_reset_after_transaction(
        self,
        integration_engine,
    ) -> None:
        """After a transaction commits/rolls back, SET LOCAL context is cleared."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        SessionLocal = async_sessionmaker(
            integration_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        sentinel = str(uuid4())

        async with SessionLocal() as session:
            await session.begin()
            await session.execute(
                text("SET LOCAL app.current_tenant = :tid"),
                {"tid": sentinel},
            )
            # Within the transaction, context is set
            result = (
                await session.execute(
                    text("SELECT current_setting('app.current_tenant', TRUE) AS tid")
                )
            ).scalar_one()
            assert result == sentinel
            await session.rollback()

        # After rollback in a new connection, context must be gone
        async with SessionLocal() as session:
            result2 = (
                await session.execute(
                    text("SELECT current_setting('app.current_tenant', TRUE) AS tid")
                )
            ).scalar_one()
            assert result2 != sentinel, (
                "Tenant context leaked across transactions — SET LOCAL did not reset properly"
            )
