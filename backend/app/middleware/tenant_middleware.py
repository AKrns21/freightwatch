"""Tenant context middleware and dependency for Row Level Security.

CRITICAL: All tenant-scoped queries MUST use get_current_tenant_db() to ensure
SET LOCAL app.current_tenant is applied before any SELECT/INSERT/UPDATE/DELETE.

The middleware extracts tenant_id from the JWT 'tid' claim (set by JWTAuthMiddleware)
and exposes get_current_tenant_db() as a FastAPI dependency.
"""

from collections.abc import AsyncGenerator, Callable

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.db.session import AsyncSessionLocal
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Extracts tenant_id from JWT (request.state) and logs tenant context.

    JWTAuthMiddleware must run before this middleware and populate
    request.state.tenant_id from the JWT 'tid' claim.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id:
            logger.debug("tenant_context_set", tenant_id=tenant_id, path=request.url.path)
        return await call_next(request)


async def get_current_tenant_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: DB session with RLS tenant context.

    Reads tenant_id from request.state (populated by JWTAuthMiddleware)
    and executes SET LOCAL app.current_tenant before yielding the session.

    Usage:
        @router.get("/shipments")
        async def list_shipments(db: AsyncSession = Depends(get_current_tenant_db)):
            result = await db.execute(select(Shipment))  # RLS filters by tenant
            ...

    Raises:
        HTTPException 401: If tenant_id is missing from the request state.
    """
    tenant_id: str | None = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(
            status_code=401,
            detail=(
                "Tenant context not available — ensure a valid JWT with 'tid' claim is provided."
            ),
        )

    async with AsyncSessionLocal() as session:
        try:
            # SET LOCAL resets automatically at transaction end — never use SET SESSION
            await session.execute(
                text("SET LOCAL app.current_tenant = :tid"),
                {"tid": tenant_id},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
