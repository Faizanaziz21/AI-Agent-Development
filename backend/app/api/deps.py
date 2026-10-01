from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base, get_session
from app.core.security import Principal, decode_access_token
from app.models import User

bearer = HTTPBearer(auto_error=False)

# Columns that must never leave the API, whatever model they appear on.
NEVER_SERIALIZE = {"password_hash", "ciphertext", "webhook_secret_enc", "embedding"}

SessionDep = Depends(get_session)


async def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    token = creds.credentials if creds else request.query_params.get("access_token")
    if not token:
        raise HTTPException(401, "authentication required")
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "invalid or expired token") from exc
    user = (await session.execute(select(User).where(User.id == claims.get("sub")))).scalar_one_or_none()
    if user is None or not user.is_active or user.org_id != claims.get("org"):
        raise HTTPException(401, "user is not active")
    p = Principal(user.id, user.org_id, user.role, user.email, user.name)
    request.state.principal = p
    await release(session)
    return p


async def release(session: AsyncSession) -> None:
    """End the request transaction before calling services that open their own sessions.

    SQLite transactions take the database write lock up front (see core.db), so a request
    session left open while the orchestrator or model gateway writes would block them."""
    await session.commit()


def require(permission: str) -> Callable[..., Any]:
    async def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.can(permission):
            raise HTTPException(403, f"permission '{permission}' required")
        return p

    return dep


async def get_owned(session: AsyncSession, model: type[Base], id: str, p: Principal) -> Any:
    """Load a tenant-owned row; rows from other organizations are indistinguishable from missing ones."""
    row = await session.get(model, id)
    if row is None or getattr(row, "org_id", None) != p.org_id:
        raise HTTPException(404, f"{model.__name__} not found")
    return row


def _value(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def to_dict(row: Any, *, exclude: set[str] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    skip = NEVER_SERIALIZE | (exclude or set())
    out = {c.key: _value(getattr(row, c.key)) for c in row.__table__.columns if c.key not in skip}
    if extra:
        out.update(extra)
    return out


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "unknown"
