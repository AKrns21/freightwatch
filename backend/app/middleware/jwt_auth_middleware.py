"""JWT Authentication middleware for FastAPI.

Validates JWT tokens (signature + expiration) for all protected endpoints.
Token is extracted from httpOnly cookie or Authorization: Bearer header.

Public endpoints (no auth required):
- /health, /docs, /redoc, /openapi.json
- /api/auth/* (login, token endpoints)
"""

from collections.abc import Callable

import jwt
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """JWT Authentication middleware.

    Validates JWT tokens and populates request.state with:
    - user_id: str (from 'sub' claim)
    - tenant_id: str | None (from 'tid' claim)
    """

    PUBLIC_PATHS: set[str] = {"/", "/health", "/docs", "/redoc", "/openapi.json"}
    PUBLIC_PATH_PREFIXES: set[str] = {"/api/auth/"}

    def __init__(self, app, secret_key: str | None = None, algorithm: str | None = None):
        super().__init__(app)
        self.secret_key = secret_key or settings.jwt_secret_key
        self.algorithm = algorithm or settings.jwt_algorithm
        logger.info("jwt_auth_middleware_enabled", algorithm=self.algorithm)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        for prefix in self.PUBLIC_PATH_PREFIXES:
            if request.url.path.startswith(prefix):
                return await call_next(request)

        if request.method == "OPTIONS":
            return await call_next(request)

        token = self._extract_token(request)
        if not token:
            logger.warning(
                "jwt_missing",
                path=request.url.path,
                method=request.method,
                client_host=request.client.host if request.client else "unknown",
            )
            return self._unauthorized_response(request, "Missing authentication token")

        auth_result = self._verify_token(token)
        if not auth_result["valid"]:
            logger.warning(
                "jwt_authentication_failed",
                path=request.url.path,
                error=auth_result["error"],
            )
            return self._unauthorized_response(request, auth_result["error"])

        request.state.user_id = auth_result["user_id"]
        request.state.tenant_id = auth_result.get("tenant_id")

        return await call_next(request)

    def _extract_token(self, request: Request) -> str | None:
        """Extract JWT from httpOnly cookie or Authorization: Bearer header."""
        token = request.cookies.get(settings.cookie_name)
        if token:
            return token

        auth_header = request.headers.get("Authorization")
        if auth_header:
            try:
                scheme, value = auth_header.split(" ", 1)
                if scheme.lower() == "bearer":
                    return value.strip()
            except ValueError:
                logger.warning("malformed_auth_header")
        return None

    def _verify_token(self, token: str) -> dict:
        """Verify JWT signature and expiration (stateless — no DB lookup in Phase 1)."""
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"require": ["sub", "exp", "jti"]},
            )
            user_id = payload.get("sub")
            if not user_id:
                return {"valid": False, "error": "Missing sub claim"}

            return {
                "valid": True,
                "user_id": user_id,
                "tenant_id": payload.get("tid"),  # FreightWatch tenant claim
            }
        except jwt.ExpiredSignatureError:
            return {"valid": False, "error": "Token has expired"}
        except jwt.InvalidSignatureError:
            return {"valid": False, "error": "Invalid token signature"}
        except jwt.DecodeError:
            return {"valid": False, "error": "Invalid token format"}
        except jwt.MissingRequiredClaimError as e:
            return {"valid": False, "error": f"Missing required claim: {e}"}
        except Exception as e:
            logger.error("jwt_verification_error", error=str(e), error_type=type(e).__name__)
            return {"valid": False, "error": "Token verification failed"}

    def _unauthorized_response(self, request: Request, error_message: str) -> JSONResponse:
        headers: dict[str, str] = {"WWW-Authenticate": 'Bearer realm="FreightWatch"'}

        origin = request.headers.get("origin")
        if origin and (origin in settings.cors_origins or "*" in settings.cors_origins):
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Access-Control-Allow-Methods"] = ", ".join(settings.cors_allowed_methods)
            headers["Access-Control-Allow-Headers"] = ", ".join(settings.cors_allowed_headers)

        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required", "error": error_message},
            headers=headers,
        )
