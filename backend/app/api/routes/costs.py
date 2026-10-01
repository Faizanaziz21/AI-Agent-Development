from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_principal, to_dict
from app.core.db import get_session
from app.core.security import Principal
from app.models import ModelUsage, Organization, Project
from app.services.budget import month_start

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get("/summary")
async def summary(days: int = Query(30, ge=1, le=365), p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    since = datetime.now(UTC) - timedelta(days=days)
    base = [ModelUsage.org_id == p.org_id, ModelUsage.created_at >= since]
    totals = (await s.execute(select(
        func.count(), func.sum(ModelUsage.cost_usd), func.sum(ModelUsage.input_tokens), func.sum(ModelUsage.output_tokens),
        func.avg(ModelUsage.latency_ms), func.sum(cast(~ModelUsage.success, Integer)),
        func.sum(cast(ModelUsage.fallback_from.is_not(None), Integer)), func.sum(cast(ModelUsage.downgraded, Integer)),
    ).where(*base))).one()

    async def group(col, limit: int = 50):
        rows = (await s.execute(select(col, func.count(), func.sum(ModelUsage.cost_usd),
                                       func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens), func.avg(ModelUsage.latency_ms))
                                .where(*base).group_by(col).order_by(func.sum(ModelUsage.cost_usd).desc()).limit(limit))).all()
        return [{"key": k, "calls": n, "cost_usd": round(c or 0, 5), "tokens": t or 0, "avg_latency_ms": round(lat or 0)}
                for k, n, c, t, lat in rows]

    day = func.date(ModelUsage.created_at)
    daily = (await s.execute(select(day, func.sum(ModelUsage.cost_usd), func.count()).where(*base).group_by(day).order_by(day))).all()
    by_project = (await s.execute(
        select(Project.id, Project.name, Project.status, Project.budget_usd, Project.spent_usd, Project.tokens_used)
        .where(Project.org_id == p.org_id, Project.created_at >= since).order_by(Project.spent_usd.desc()).limit(50))).all()
    org = await s.get(Organization, p.org_id)
    month_spent = (await s.execute(select(func.coalesce(func.sum(ModelUsage.cost_usd), 0.0)).where(
        ModelUsage.org_id == p.org_id, ModelUsage.created_at >= month_start()))).scalar_one()
    return {
        "period_days": days,
        "totals": {"calls": totals[0], "cost_usd": round(totals[1] or 0, 4), "input_tokens": totals[2] or 0,
                   "output_tokens": totals[3] or 0, "avg_latency_ms": round(totals[4] or 0), "failures": totals[5] or 0,
                   "fallbacks": totals[6] or 0, "downgrades": totals[7] or 0},
        "budget": {"monthly_budget_usd": org.monthly_budget_usd, "month_spent_usd": round(month_spent, 4),
                   "ratio": round(month_spent / org.monthly_budget_usd, 4) if org.monthly_budget_usd else None},
        "by_provider": await group(ModelUsage.provider),
        "by_model": await group(ModelUsage.model),
        "by_agent": await group(ModelUsage.agent_key),
        "by_purpose": await group(ModelUsage.purpose),
        "daily": [{"day": str(d), "cost_usd": round(c or 0, 4), "calls": n} for d, c, n in daily],
        "by_project": [{"id": i, "name": n, "status": st, "budget_usd": b, "spent_usd": round(sp, 4), "tokens": tk,
                        "ratio": round(sp / b, 4) if b else None} for i, n, st, b, sp, tk in by_project],
    }


@router.get("/usage")
async def usage(project_id: str | None = None, limit: int = Query(200, le=2000), p: Principal = Depends(current_principal),
                s: AsyncSession = Depends(get_session)):
    q = select(ModelUsage).where(ModelUsage.org_id == p.org_id)
    if project_id:
        q = q.where(ModelUsage.project_id == project_id)
    rows = (await s.execute(q.order_by(ModelUsage.created_at.desc()).limit(limit))).scalars()
    return [to_dict(r) for r in rows]
