"""Request tracking middleware — adds request ID and logs request lifecycle."""

import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.logger import (
    clear_request_context,
    get_logger,
    set_request_id,
    set_tenant_id,
    set_user_id,
)

logger = get_logger(__name__)


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking requests with IDs and structured log context.

    - Generates or forwards X-Request-ID
    - Sets request_id, tenant_id, user_id into logging context
    - Logs request start and completion with duration
    - Clears context after each request
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        set_request_id(request_id)

        # Populate logging context from request state (set by JWT + tenant middleware)
        user_id = getattr(request.state, "user_id", None)
        tenant_id = getattr(request.state, "tenant_id", None)
        if user_id:
            set_user_id(user_id)
        if tenant_id:
            set_tenant_id(tenant_id)

        logger.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            client_host=request.client.host if request.client else "unknown",
        )

        try:
            start_time = time.time()
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000

            response.headers["X-Request-ID"] = request_id

            logger.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            return response

        except Exception as e:
            logger.error("request_error", error=str(e), error_type=type(e).__name__)
            raise

        finally:
            clear_request_context()
