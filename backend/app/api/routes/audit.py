from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, to_dict
from app.core.db import get_session
from app.core.security import Principal
from app.models import AuditLog
from app.services import audit

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def list_audit(action: str | None = None, actor_id: str | None = None, project_id: str | None = None,
                     resource_type: str | None = None, before_seq: int | None = None, limit: int = Query(200, le=2000),
                     p: Principal = Depends(require("audit.read")), s: AsyncSession = Depends(get_session)):
    q = select(AuditLog).where(AuditLog.org_id == p.org_id)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    if project_id:
        q = q.where(AuditLog.project_id == project_id)
    if resource_type:
        q = q.where(AuditLog.resource_type == resource_type)
    if before_seq:
        q = q.where(AuditLog.seq < before_seq)
    rows = (await s.execute(q.order_by(AuditLog.seq.desc()).limit(limit))).scalars()
    return [to_dict(r) for r in rows]


@router.get("/verify")
async def verify(p: Principal = Depends(require("audit.read")), s: AsyncSession = Depends(get_session)):
    return await audit.verify_chain(s, p.org_id)
