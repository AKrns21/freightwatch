"""Database session management."""

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models.base import Base
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _fix_uvloop_dns() -> None:
    """Fix uvloop DNS resolution by pre-resolving hostname using asyncio.

    uvloop 0.22.x + asyncpg 0.30.x has socket.gaierror on macOS when
    resolving hostnames. Pre-resolving forces the standard DNS resolver.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.__class__.__name__ == "Loop":  # uvloop detected
            import socket
            import urllib.parse

            url = urllib.parse.urlparse(
                settings.database_url.replace("postgresql+asyncpg://", "http://")
            )
            hostname = url.hostname
            if hostname and not hostname.replace(".", "").isdigit():
                try:
                    socket.getaddrinfo(hostname, None, socket.AF_INET)
                    logger.info("dns_preresolution_successful", hostname=hostname)
                except Exception as e:
                    logger.warning("dns_preresolution_failed", hostname=hostname, error=str(e))
    except Exception as e:
        logger.warning("uvloop_dns_fix_failed", error=str(e))


_fix_uvloop_dns()

# SSL connect_args for Supabase pooler (Supavisor requires SSL)
_connect_args: dict = {
    "server_settings": {"jit": "off"},
    "command_timeout": 120,
}
if settings.db_ssl_required:
    _connect_args["ssl"] = "require"

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Verify database connection on startup."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("database_connection_verified", mode=settings.app_env)
    except Exception as e:
        logger.error("database_initialization_failed", error=str(e))
        raise


async def close_db() -> None:
    """Close database connections."""
    try:
        await engine.dispose()
        logger.info("database_connections_closed")
    except Exception as e:
        logger.error("database_close_failed", error=str(e))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: database session without tenant context.

    Use get_tenant_db() for queries on tenant-scoped tables.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_tenant_db(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: database session with RLS tenant context.

    Sets SET LOCAL app.current_tenant before yielding, which is automatically
    cleared at transaction end.

    Usage:
        @router.get("/shipments")
        async def list_shipments(db: AsyncSession = Depends(get_current_tenant_db)):
            ...

    Note: Use get_current_tenant_db() (from app.middleware.tenant_middleware) which
    automatically extracts tenant_id from the request state.
    """
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": tenant_id},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
