"""JWT token generation and verification for FreightWatch.

Stateless JWT operations only — no database token storage in Phase 1.
Tokens are validated by signature + expiration.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class JWTService:
    """Service for generating and validating JWT tokens."""

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
