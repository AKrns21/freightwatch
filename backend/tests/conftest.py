"""Shared pytest fixtures for FreightWatch test suite.

Integration test setup:
- Reads TEST_TENANT_A_ID and TEST_TENANT_B_ID from environment.
- Both must be pre-existing tenants in the test Supabase project.
- Each fixture yields an AsyncSession with the correct tenant context set via
  SET LOCAL app.current_tenant (mirrors production RLS behaviour exactly).
- Tests are wrapped in a transaction that is ALWAYS rolled back — data never
  persists between runs.

To run integration tests:
  export TEST_TENANT_A_ID=<uuid>
  export TEST_TENANT_B_ID=<uuid>
  pytest tests/integration/ -v
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Engine — shared across the integration test session
# ---------------------------------------------------------------------------

_DATABASE_URL = os.getenv("DATABASE_URL", "")
_DB_SSL = os.getenv("DB_SSL_REQUIRED", "true").lower() == "true"

_connect_args: dict = {
    "server_settings": {"jit": "off"},
    "command_timeout": 60,
}
if _DB_SSL:
    _connect_args["ssl"] = "require"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def integration_engine():
    """Create a SQLAlchemy async engine for the integration test session.

    Skips if DATABASE_URL is not configured.
    """
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL not set — skipping integration tests")

    engine = create_async_engine(
        _DATABASE_URL,
        echo=False,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args=_connect_args,
    )
    yield engine
    import asyncio

    asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.fixture(scope="session")
def tenant_a_id() -> str:
    tid = os.getenv("TEST_TENANT_A_ID", "")
    if not tid:
        pytest.skip("TEST_TENANT_A_ID not set")
    return tid


@pytest.fixture(scope="session")
def tenant_b_id() -> str:
    tid = os.getenv("TEST_TENANT_B_ID", "")
    if not tid:
        pytest.skip("TEST_TENANT_B_ID not set")
    return tid


# ---------------------------------------------------------------------------
# Per-test DB sessions — always rolled back
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def tenant_a_db(integration_engine, tenant_a_id: str) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession with tenant A's RLS context, rolled back after each test."""
    SessionLocal = async_sessionmaker(
        integration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with SessionLocal() as session:
        await session.begin()
        await session.execute(
            text("SET LOCAL app.current_tenant = :tid"),
            {"tid": tenant_a_id},
        )
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def tenant_b_db(integration_engine, tenant_b_id: str) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession with tenant B's RLS context, rolled back after each test."""
    SessionLocal = async_sessionmaker(
        integration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with SessionLocal() as session:
        await session.begin()
        await session.execute(
            text("SET LOCAL app.current_tenant = :tid"),
            {"tid": tenant_b_id},
        )
        try:
            yield session
        finally:
            await session.rollback()
