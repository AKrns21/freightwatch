"""JWT token generation and verification for FreightWatch.

Stateless JWT operations only — no database token storage in Phase 1.
Tokens are validated by signature + expiration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


class JWTService:
    """Service for generating and validating JWT tokens."""

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    @staticmethod
    def generate_jwt_token(
        user_id: str,
        tenant_id: str,
        expires_in_days: int | None = None,
        token_id: str | None = None,
    ) -> tuple[str, datetime, str]:
        """Generate a signed JWT token.

        Args:
            user_id: User identifier (sub claim).
            tenant_id: Tenant UUID (tid claim, used for RLS context).
            expires_in_days: Token validity period (default: from settings).
            token_id: Optional UUID string for jti claim (generated if not provided).

        Returns:
            Tuple of (jwt_token, expires_at_datetime, token_id).
        """
        if not token_id:
            token_id = str(uuid4())

        expires_in_days = expires_in_days or settings.jwt_expiration_days
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=expires_in_days)

        payload = {
            "sub": user_id,
            "tid": tenant_id,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": token_id,
        }

        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

        logger.info(
            "jwt_token_generated",
            user_id=user_id,
            tenant_id=tenant_id,
            token_id=token_id,
            expires_in_days=expires_in_days,
        )

        return token, expires_at, token_id

    @staticmethod
    def decode_jwt_token(token: str) -> dict | None:
        """Decode and validate a JWT token (signature + expiration).

        Args:
            token: JWT token string.

        Returns:
            Decoded payload dict or None if invalid/expired.
        """
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
                options={"require": ["sub", "exp", "jti"]},
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("jwt_decode_failed", reason="expired")
            return None
        except jwt.InvalidSignatureError:
            logger.warning("jwt_decode_failed", reason="invalid_signature")
            return None
        except jwt.DecodeError:
            logger.warning("jwt_decode_failed", reason="invalid_format")
            return None
        except Exception as e:
            logger.error("jwt_decode_error", error=str(e), error_type=type(e).__name__)
            return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_jwt_service: JWTService | None = None


def get_jwt_service() -> JWTService:
    """Return the module-level JWTService singleton."""
    global _jwt_service
    if _jwt_service is None:
        _jwt_service = JWTService()
    return _jwt_service
