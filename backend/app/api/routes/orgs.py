from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_principal, get_owned, require, to_dict
from app.core.crypto import decrypt, encrypt, mask
from app.core.db import get_session
from app.core.security import ROLES, Principal, hash_password
from app.models import Notification, Organization, Project, Secret, User, Workspace
from app.services import audit

router = APIRouter(tags=["organization"])

EDITABLE_SETTINGS = {"quality_threshold", "default_project_budget_usd", "communication_rules", "max_revisions", "auto_approve_low_risk"}


@router.get("/org")
async def get_org(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    org = await s.get(Organization, p.org_id)
    workspaces = (await s.execute(select(Workspace).where(Workspace.org_id == p.org_id))).scalars()
    counts = dict((await s.execute(select(Project.status, func.count()).where(Project.org_id == p.org_id)
                                   .group_by(Project.status))).all())
    users = (await s.execute(select(func.count()).select_from(User).where(User.org_id == p.org_id))).scalar_one()
    return {**to_dict(org), "workspaces": [to_dict(w) for w in workspaces], "project_counts": counts, "user_count": users,
            "webhook_url": f"/api/v1/webhooks/support/{org.slug}"}


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    monthly_budget_usd: float | None = Field(default=None, ge=0, le=10_000_000)
    settings: dict[str, Any] | None = None


@router.patch("/org")
async def update_org(body: OrgUpdate, p: Principal = Depends(require("settings.write")), s: AsyncSession = Depends(get_session)):
    org = await s.get(Organization, p.org_id)
    changes: dict[str, Any] = {}
    if body.name is not None:
        org.name = changes["name"] = body.name
    if body.monthly_budget_usd is not None:
        org.monthly_budget_usd = changes["monthly_budget_usd"] = body.monthly_budget_usd
    if body.settings is not None:
        unknown = set(body.settings) - EDITABLE_SETTINGS
        if unknown:
            raise HTTPException(422, f"unknown settings: {sorted(unknown)}")
        org.settings = {**(org.settings or {}), **body.settings}
        changes["settings"] = body.settings
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="org.updated", resource_type="organization",
                       resource_id=org.id, details=changes)
    return to_dict(org)


@router.post("/org/webhook-secret/rotate")
async def rotate_webhook_secret(p: Principal = Depends(require("settings.write")), s: AsyncSession = Depends(get_session)):
    import secrets as pysecrets

    org = await s.get(Organization, p.org_id)
    value = "whsec_" + pysecrets.token_urlsafe(32)
    org.webhook_secret_enc = encrypt(value)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="org.webhook_secret_rotated",
                       resource_type="organization", resource_id=org.id)
    # shown once; only the ciphertext is stored
    return {"webhook_secret": value}


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


@router.post("/org/workspaces", status_code=201)
async def create_workspace(body: WorkspaceIn, p: Principal = Depends(require("settings.write")), s: AsyncSession = Depends(get_session)):
    ws = Workspace(org_id=p.org_id, name=body.name, description=body.description)
    s.add(ws)
    await s.flush()
    return to_dict(ws)


@router.get("/users")
async def list_users(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(User).where(User.org_id == p.org_id).order_by(User.created_at))).scalars()
    return [to_dict(u) for u in rows]


class UserIn(BaseModel):
    email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=1, max_length=200)
    role: str
    title: str = Field(default="", max_length=120)
    password: str = Field(min_length=10, max_length=200)


@router.post("/users", status_code=201)
async def create_user(body: UserIn, p: Principal = Depends(require("user.write")), s: AsyncSession = Depends(get_session)):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if body.role == "owner" and p.role != "owner":
        raise HTTPException(403, "only owners can create owners")
    email = body.email.strip().lower()
    if (await s.execute(select(User.id).where(User.email == email))).first():
        raise HTTPException(409, "email already registered")
    u = User(org_id=p.org_id, email=email, name=body.name, role=body.role, title=body.title, password_hash=hash_password(body.password))
    s.add(u)
    await s.flush()
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="user.created", resource_type="user",
                       resource_id=u.id, details={"email": email, "role": body.role})
    return to_dict(u)


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    title: str | None = Field(default=None, max_length=120)


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, p: Principal = Depends(require("user.write")),
                      s: AsyncSession = Depends(get_session)):
    u = await get_owned(s, User, user_id, p)
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(422, f"role must be one of {ROLES}")
        if "owner" in (body.role, u.role) and p.role != "owner":
            raise HTTPException(403, "only owners can grant or revoke owner")
        u.role = body.role
    if body.is_active is not None:
        if u.id == p.user_id and not body.is_active:
            raise HTTPException(400, "you cannot deactivate yourself")
        u.is_active = body.is_active
    if body.title is not None:
        u.title = body.title
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="user.updated", resource_type="user",
                       resource_id=u.id, details=body.model_dump(exclude_none=True))
    return to_dict(u)


@router.get("/secrets")
async def list_secrets(p: Principal = Depends(require("settings.write")), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Secret).where(Secret.org_id == p.org_id).order_by(Secret.name))).scalars()
    return [to_dict(x, extra={"preview": mask(decrypt(x.ciphertext))}) for x in rows]


class SecretIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=8000)
    description: str = Field(default="", max_length=500)


@router.put("/secrets")
async def put_secret(body: SecretIn, p: Principal = Depends(require("settings.write")), s: AsyncSession = Depends(get_session)):
    row = (await s.execute(select(Secret).where(Secret.org_id == p.org_id, Secret.name == body.name))).scalar_one_or_none()
    if row is None:
        row = Secret(org_id=p.org_id, name=body.name, ciphertext="", created_by=p.user_id)
        s.add(row)
    row.ciphertext = encrypt(body.value)
    row.description = body.description
    await s.flush()
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="secret.written", resource_type="secret",
                       resource_id=row.id, details={"name": body.name})
    return to_dict(row, extra={"preview": mask(body.value)})


@router.delete("/secrets/{secret_id}", status_code=204)
async def delete_secret(secret_id: str, p: Principal = Depends(require("settings.write")), s: AsyncSession = Depends(get_session)):
    row = await get_owned(s, Secret, secret_id, p)
    await s.delete(row)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="secret.deleted", resource_type="secret",
                       resource_id=secret_id, details={"name": row.name})


@router.get("/notifications")
async def notifications(unread: bool = False, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    q = select(Notification).where(Notification.org_id == p.org_id,
                                   (Notification.user_id == p.user_id) | (Notification.user_id.is_(None)))
    if unread:
        q = q.where(Notification.read.is_(False))
    rows = (await s.execute(q.order_by(Notification.created_at.desc()).limit(100))).scalars()
    return [to_dict(n) for n in rows]


@router.post("/notifications/read-all", status_code=204)
async def read_all(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    await s.execute(update(Notification).where(Notification.org_id == p.org_id,
                                               (Notification.user_id == p.user_id) | (Notification.user_id.is_(None)))
                    .values(read=True))
