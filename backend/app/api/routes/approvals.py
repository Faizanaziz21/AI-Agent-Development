from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_principal, get_owned, require, to_dict
from app.core.db import get_session
from app.core.security import Principal, can_decide_approval
from app.models import Approval, Project, Task
from app.services import approvals as approval_service
from app.services import orchestrator

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(status: str | None = "PENDING", project_id: str | None = None, limit: int = Query(200, le=1000),
                         p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    q = select(Approval, Project.name).outerjoin(Project, Project.id == Approval.project_id).where(Approval.org_id == p.org_id)
    if status and status != "ALL":
        q = q.where(Approval.status == status)
    if project_id:
        q = q.where(Approval.project_id == project_id)
    rows = (await s.execute(q.order_by(Approval.created_at.desc()).limit(limit))).all()
    return [to_dict(a, extra={"project_name": name, "can_decide": a.status == "PENDING" and can_decide_approval(p.role, a.required_role)})
            for a, name in rows]


@router.get("/summary")
async def summary(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Approval.status, Approval.risk_level, func.count()).where(Approval.org_id == p.org_id)
                            .group_by(Approval.status, Approval.risk_level))).all()
    out: dict[str, Any] = {"by_status": {}, "pending_by_risk": {}}
    for st, risk, n in rows:
        out["by_status"][st] = out["by_status"].get(st, 0) + n
        if st == "PENDING":
            out["pending_by_risk"][risk] = n
    return out


@router.get("/{approval_id}")
async def get_approval(approval_id: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    a = await get_owned(s, Approval, approval_id, p)
    task = await s.get(Task, a.task_id) if a.task_id else None
    return to_dict(a, extra={"task": to_dict(task, exclude={"checkpoint"}) if task else None,
                             "can_decide": a.status == "PENDING" and can_decide_approval(p.role, a.required_role)})


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject", "edit", "request_changes"]
    comment: str = Field(default="", max_length=4000)
    edited_payload: dict[str, Any] | None = None


@router.post("/{approval_id}/decision")
async def decide(approval_id: str, body: DecisionIn, p: Principal = Depends(require("approval.decide")),
                 s: AsyncSession = Depends(get_session)):
    a = await get_owned(s, Approval, approval_id, p)
    try:
        await approval_service.decide(s, a, p, body.decision, body.edited_payload, body.comment)
    except approval_service.ApprovalError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    await s.commit()
    # resume the paused task now that the decision is durable
    await orchestrator.apply_approval(a.id)
    await s.refresh(a)
    return to_dict(a)
