"""FreightWatch FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.session import close_db, init_db
from app.middleware import (
    JWTAuthMiddleware,
    RequestTrackingMiddleware,
    SecurityHeadersMiddleware,
    TenantContextMiddleware,
)
from app.routers import upload as upload_router
from app.services.upload_processor_service import start_stale_watcher
from app.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    setup_logging()
    await init_db()
    start_stale_watcher()
    logger.info("freightwatch_started", version=settings.app_version, env=settings.app_env)
    yield
    await close_db()
    logger.info("freightwatch_stopped")


_docs_url = "/docs" if settings.debug else None
_redoc_url = "/redoc" if settings.debug else None
_openapi_url = "/openapi.json" if settings.debug else None

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

# CORS — must be first
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=settings.cors_allowed_methods,
    allow_headers=settings.cors_allowed_headers,
    max_age=settings.cors_max_age,
)

# Request tracking (adds request_id to all logs)
app.add_middleware(RequestTrackingMiddleware)

# Tenant context extraction (reads request.state from JWT middleware)
app.add_middleware(TenantContextMiddleware)

# JWT auth — validates tokens and populates request.state.user_id / tenant_id
if settings.jwt_auth_enabled:
    app.add_middleware(JWTAuthMiddleware)
    logger.info("jwt_auth_enabled")
else:
    logger.warning("jwt_auth_disabled", note="All endpoints publicly accessible")

# Security headers — must be outermost to cover all responses
app.add_middleware(SecurityHeadersMiddleware, enabled=settings.security_headers_enabled)


app.include_router(upload_router.router, prefix=settings.api_prefix)


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Health check — always public, no auth required."""
    return {"status": "ok"}
