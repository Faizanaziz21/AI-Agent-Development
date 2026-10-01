from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, current_principal, to_dict
from app.core.db import get_session
from app.core.security import ROLE_PERMISSIONS, Principal, create_access_token, verify_password
from app.models import Organization, User
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


@router.post("/login")
async def login(body: LoginIn, request: Request, s: AsyncSession = Depends(get_session)):
    user = (await s.execute(select(User).where(User.email == body.email.strip().lower()))).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        if user is not None:
            await audit.record(s, user.org_id, actor_type="user", actor_id=user.id, action="auth.login_failed",
                               resource_type="user", resource_id=user.id, ip=client_ip(request))
        raise HTTPException(401, "invalid email or password")
    org = await s.get(Organization, user.org_id)
    await audit.record(s, user.org_id, actor_type="user", actor_id=user.id, action="auth.login", resource_type="user",
                       resource_id=user.id, ip=client_ip(request))
    return {"access_token": create_access_token(user.id, user.org_id, user.role), "token_type": "bearer",
            "user": to_dict(user), "organization": to_dict(org), "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set()))}


@router.get("/me")
async def me(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    user = await s.get(User, p.user_id)
    org = await s.get(Organization, p.org_id)
    return {"user": to_dict(user), "organization": to_dict(org), "permissions": sorted(ROLE_PERMISSIONS.get(p.role, set()))}
