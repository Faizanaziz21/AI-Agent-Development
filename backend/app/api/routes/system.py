from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_principal, require
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import Principal
from app.core.telemetry import APPROVALS_PENDING, QUEUE_DEPTH
from app.models import Approval, Task, TaskStatus
from app.services import orchestrator
from app.services.model_gateway.catalog import CATALOG, PURPOSE_TIER
from app.services.model_gateway.gateway import get_gateway
from app.services.queue import get_queue
from app.services.worker import get_pool

router = APIRouter(tags=["system"])


@router.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request, s: AsyncSession = Depends(get_session)):
    checks: dict[str, str] = {}
    try:
        await s.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
    try:
        await get_queue().depth()
        checks["queue"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["queue"] = f"error: {exc}"
    if not getattr(request.app.state, "ready", False):
        checks["startup"] = "pending"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        raise HTTPException(503, {"status": "not ready", "checks": checks})
    return {"status": "ready", "checks": checks}


@router.get("/metrics", include_in_schema=False)
async def metrics(s: AsyncSession = Depends(get_session)):
    try:
        QUEUE_DEPTH.set(await get_queue().depth())
        APPROVALS_PENDING.set((await s.execute(select(func.count()).select_from(Approval).where(Approval.status == "PENDING"))).scalar_one())
    except Exception:  # noqa: BLE001 - metrics must never fail the scrape
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/system/status", tags=["system"])
async def status(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    settings = get_settings()
    pool = get_pool()
    gw = get_gateway()
    task_counts = dict((await s.execute(select(Task.status, func.count()).where(Task.org_id == p.org_id)
                                        .group_by(Task.status))).all())
    return {
        "environment": settings.env,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "queue": {"backend": "redis" if settings.redis_url else "in-memory", "depth": await get_queue().depth()},
        "workers": {"embedded": pool is not None, "concurrency": pool.concurrency if pool else 0,
                    "busy": pool.busy if pool else 0, "processed": pool.processed if pool else 0, "name": pool.name if pool else None},
        "providers": gw.status(),
        "provider_order": gw.router.provider_order,
        "models": [asdict(m) for m in CATALOG],
        "purpose_tiers": PURPOSE_TIER,
        "limits": {"max_delegation_depth": settings.max_delegation_depth, "max_children_per_task": settings.max_children_per_task,
                   "max_tasks_per_project": settings.max_tasks_per_project, "max_revisions": settings.max_revisions,
                   "default_quality_threshold": settings.default_quality_threshold,
                   "rate_limit_per_minute": settings.rate_limit_per_minute},
        "embedding_provider": settings.embedding_provider,
        "vector_backend": settings.vector_backend,
        "tasks": task_counts,
        "active_tasks": sum(task_counts.get(st, 0) for st in TaskStatus.ACTIVE),
    }


@router.post("/api/v1/system/recover", tags=["system"])
async def recover(_: Principal = Depends(require("settings.write"))):
    return await orchestrator.recover(stale_queued_after_s=0)
