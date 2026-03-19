"""Auth router — login endpoint.

POST /api/auth/login  → JWT token response

Port of backend_legacy/src/modules/auth/auth.controller.ts
Issue: #50

Key features:
- Password verification via bcrypt
- JWT issuance with tenant_id (tid) claim for downstream RLS context
- Public endpoint — no JWT required on the request

Note on RLS: The users table is tenant-scoped. For login to work without a
prior tenant context, the RLS policy on the `users` table must allow SELECT
for the freightwatch_app role when app.current_tenant is not set
(e.g. USING (TRUE) for SELECT, or USING (tenant_id::text =
COALESCE(current_setting('app.current_tenant', TRUE), tenant_id::text))).
Adjust the Supabase RLS policy if login returns 401 for valid credentials.
"""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import User
from app.services.jwt_service import get_jwt_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    token: str
    expires_at: datetime
    token_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Authenticate user and return a JWT token.

    The token includes the tenant_id (tid claim) which is used by all
    subsequent requests to set the PostgreSQL RLS tenant context.
    """
    user = (
        await db.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()

    if user is None or not _pwd_context.verify(body.password, user.password_hash):
        logger.warning("login_failed", email=body.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        logger.warning("login_inactive_user", user_id=str(user.id))
        raise HTTPException(status_code=403, detail="Account is inactive")

    jwt_svc = get_jwt_service()
    token, expires_at, token_id = jwt_svc.generate_jwt_token(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
    )

    logger.info("login_successful", user_id=str(user.id), tenant_id=str(user.tenant_id))

    return LoginResponse(token=token, expires_at=expires_at, token_id=token_id)
