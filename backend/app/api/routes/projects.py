from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import current_principal, get_owned, release, require, to_dict
from app.core.db import get_session, session_scope
from app.core.events import bus
from app.core.security import Principal
from app.models import (
    AgentDefinition,
    Approval,
    Evaluation,
    EventRecord,
    Execution,
    ExecutionStep,
    ModelUsage,
    Objective,
    Plan,
    Project,
    Task,
    TaskRevision,
    TaskStatus,
    ToolCall,
)
from app.services import orchestrator
from app.services.projects import DEMO_TICKETS, TEMPLATES, create_project, create_support_ticket

router = APIRouter(tags=["projects"])

CHAOS_KEYS = {"provider_failure_rate", "malformed_output_rate", "tool_timeout_rate", "tool_failure_rate", "invalid_response_rate",
              "worker_crash_rate"}
TASK_LIST_EXCLUDE = {"input", "output", "checkpoint", "review_criteria"}


def _validate_chaos(v: dict[str, Any]) -> dict[str, Any]:
    unknown = set(v) - CHAOS_KEYS
    if unknown:
        raise ValueError(f"unknown chaos keys {sorted(unknown)}; allowed: {sorted(CHAOS_KEYS)}")
    for k, rate in v.items():
        if not isinstance(rate, int | float) or not 0 <= rate <= 1:
            raise ValueError(f"{k} must be a number between 0 and 1")
    return v


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=10, max_length=8000)
    description: str = Field(default="", max_length=2000)
    template: str = "generic"
    parameters: dict[str, Any] = Field(default_factory=dict)
    budget_usd: float = Field(default=20.0, gt=0, le=100_000)
    chaos: dict[str, float] = Field(default_factory=dict)
    start: bool = True

    _chaos = field_validator("chaos")(_validate_chaos)


class TicketIn(BaseModel):
    customer_email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=5, max_length=20000)
    budget_usd: float = Field(default=5.0, gt=0, le=1000)
    chaos: dict[str, float] = Field(default_factory=dict)

    _chaos = field_validator("chaos")(_validate_chaos)


def _project_dict(p: Project, task_counts: dict[str, int] | None = None, pending: int = 0) -> dict:
    counts = task_counts or {}
    total = sum(counts.values())
    done = counts.get(TaskStatus.COMPLETED, 0)
    return to_dict(p, extra={"task_counts": counts, "tasks_total": total, "progress": round(done / total, 3) if total else 0.0,
                             "pending_approvals": pending})


@router.get("/projects/templates")
async def templates(_: Principal = Depends(current_principal)):
    return {"templates": [{"key": k, **v} for k, v in TEMPLATES.items()], "demo_tickets": DEMO_TICKETS,
            "chaos_keys": sorted(CHAOS_KEYS)}


@router.get("/projects")
async def list_projects(status: str | None = None, limit: int = Query(100, le=500), p: Principal = Depends(current_principal),
                        s: AsyncSession = Depends(get_session)):
    q = select(Project).where(Project.org_id == p.org_id)
    if status:
        q = q.where(Project.status == status)
    projects = list((await s.execute(q.order_by(Project.created_at.desc()).limit(limit))).scalars())
    ids = [x.id for x in projects]
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    pending: dict[str, int] = {}
    if ids:
        for pid, st, n in (await s.execute(select(Task.project_id, Task.status, func.count()).where(Task.project_id.in_(ids))
                                           .group_by(Task.project_id, Task.status))).all():
            counts[pid][st] = n
        pending = dict((await s.execute(select(Approval.project_id, func.count()).where(
            Approval.project_id.in_(ids), Approval.status == "PENDING").group_by(Approval.project_id))).all())
    return [_project_dict(x, counts.get(x.id), pending.get(x.id, 0)) for x in projects]


@router.post("/projects", status_code=201)
async def new_project(body: ProjectIn, p: Principal = Depends(require("project.write")), s: AsyncSession = Depends(get_session)):
    if body.template not in TEMPLATES:
        raise HTTPException(422, f"template must be one of {sorted(TEMPLATES)}")
    params = {**TEMPLATES[body.template]["parameters"], **body.parameters}
    proj = await create_project(s, org_id=p.org_id, user_id=p.user_id, name=body.name, objective=body.objective, parameters=params,
                                template=body.template, budget_usd=body.budget_usd, chaos=body.chaos, description=body.description)
    await s.commit()
    if body.start:
        await orchestrator.start_project(proj.id, p.user_id)
        await s.refresh(proj)
    return _project_dict(proj)


@router.post("/support/tickets", status_code=201)
async def new_ticket(body: TicketIn, p: Principal = Depends(require("project.write")), s: AsyncSession = Depends(get_session)):
    proj, ticket = await create_support_ticket(s, org_id=p.org_id, user_id=p.user_id, customer_email=body.customer_email,
                                               subject=body.subject, body=body.body, budget_usd=body.budget_usd, chaos=body.chaos)
    await s.commit()
    await orchestrator.start_project(proj.id, p.user_id)
    return {"project_id": proj.id, "ticket_id": ticket.id}


@router.get("/projects/{project_id}")
async def get_project(project_id: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    proj = await get_owned(s, Project, project_id, p)
    obj = (await s.execute(select(Objective).where(Objective.project_id == proj.id).order_by(Objective.created_at.desc()))).scalars().first()
    plan = (await s.execute(select(Plan).where(Plan.project_id == proj.id).order_by(Plan.version.desc()))).scalars().first()
    tasks = list((await s.execute(select(Task).where(Task.project_id == proj.id).order_by(Task.created_at))).scalars())
    approvals = list((await s.execute(select(Approval).where(Approval.project_id == proj.id).order_by(Approval.created_at.desc()))).scalars())
    usage = (await s.execute(select(func.count(), func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens),
                                    func.sum(ModelUsage.cost_usd), func.avg(ModelUsage.latency_ms))
                             .where(ModelUsage.project_id == proj.id))).one()
    tool_stats = dict((await s.execute(select(ToolCall.status, func.count()).where(ToolCall.project_id == proj.id)
                                       .group_by(ToolCall.status))).all())
    counts = Counter(t.status for t in tasks)
    return {
        **_project_dict(proj, dict(counts), sum(a.status == "PENDING" for a in approvals)),
        "objective": to_dict(obj) if obj else None,
        "plan": to_dict(plan) if plan else None,
        "tasks": [to_dict(t, exclude=TASK_LIST_EXCLUDE) for t in tasks],
        "approvals": [to_dict(a) for a in approvals],
        "metrics": {"model_calls": usage[0], "tokens": usage[1] or 0, "model_cost_usd": round(usage[2] or 0, 4),
                    "avg_model_latency_ms": round(usage[3] or 0), "tool_calls": tool_stats,
                    "agents": len({t.agent_instance or t.agent_key for t in tasks}),
                    "retries": sum(t.retries for t in tasks), "failures": sum(t.failures for t in tasks),
                    "revisions": sum(t.revision for t in tasks)},
    }


@router.post("/projects/{project_id}/start")
async def start(project_id: str, p: Principal = Depends(require("project.write")), s: AsyncSession = Depends(get_session)):
    await get_owned(s, Project, project_id, p)
    await release(s)
    try:
        await orchestrator.start_project(project_id, p.user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/projects/{project_id}/cancel")
async def cancel(project_id: str, p: Principal = Depends(require("project.write")), s: AsyncSession = Depends(get_session)):
    proj = await get_owned(s, Project, project_id, p)
    if proj.status in ("COMPLETED", "CANCELLED"):
        raise HTTPException(409, f"project is {proj.status}")
    await release(s)
    await orchestrator.cancel_project(project_id, p.user_id)
    return {"ok": True}


@router.get("/projects/{project_id}/graph")
async def graph(project_id: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    """Task DAG (dependencies, delegations, reviews) plus the agent-level delegation graph."""
    await get_owned(s, Project, project_id, p)
    tasks = list((await s.execute(select(Task).where(Task.project_id == project_id).order_by(Task.created_at))).scalars())
    agents = {a.key: a for a in (await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == p.org_id))).scalars()}
    by_id = {t.id: t for t in tasks}
    nodes, edges = [], []
    for t in tasks:
        a = agents.get(t.agent_key)
        review_of = (t.input or {}).get("_review_of")
        kind = "review" if review_of else "subtask" if t.parent_task_id else "task"
        nodes.append({"id": t.id, "key": t.key, "title": t.title, "status": t.status, "agent_key": t.agent_key,
                      "agent_name": a.name if a else t.agent_key, "agent_instance": t.agent_instance, "color": a.color if a else "#64748b",
                      "kind": kind, "capability": t.capability, "quality_score": t.quality_score, "revision": t.revision,
                      "attempt": t.attempt, "spent_usd": round(t.spent_usd, 4), "current_action": t.current_action,
                      "output_summary": t.output_summary, "depth": t.delegation_depth})
        for d in t.depends_on or []:
            if d in by_id:
                edges.append({"id": f"dep-{d}-{t.id}", "source": d, "target": t.id, "kind": "dependency"})
        if t.parent_task_id and t.parent_task_id in by_id:
            edges.append({"id": f"del-{t.parent_task_id}-{t.id}", "source": t.parent_task_id, "target": t.id, "kind": "delegation"})
        if review_of and review_of in by_id:
            edges.append({"id": f"rev-{review_of}-{t.id}", "source": review_of, "target": t.id, "kind": "review"})

    # agent-level view: who handed work to whom
    flows: Counter = Counter()
    for e in edges:
        src, dst = by_id[e["source"]].agent_key, by_id[e["target"]].agent_key
        if src != dst:
            flows[(src, dst, e["kind"])] += 1
    involved = {t.agent_key for t in tasks} | {"supervisor"}
    agent_nodes = []
    for key in sorted(involved):
        a = agents.get(key)
        mine = [t for t in tasks if t.agent_key == key]
        agent_nodes.append({"id": key, "name": a.name if a else key, "role": a.role if a else "", "color": a.color if a else "#64748b",
                            "tasks": len(mine), "active": sum(t.status in TaskStatus.ACTIVE for t in mine),
                            "completed": sum(t.status == TaskStatus.COMPLETED for t in mine),
                            "spent_usd": round(sum(t.spent_usd for t in mine), 4)})
    planned_roots = {t.agent_key for t in tasks if not t.depends_on and not t.parent_task_id}
    agent_edges = [{"id": f"{a}-{b}-{k}", "source": a, "target": b, "kind": k, "count": n} for (a, b, k), n in flows.items()]
    agent_edges += [{"id": f"supervisor-{k}-plan", "source": "supervisor", "target": k, "kind": "plan", "count": 1}
                    for k in sorted(planned_roots) if k != "supervisor"]
    return {"nodes": nodes, "edges": edges, "agents": {"nodes": agent_nodes, "edges": agent_edges}}


def _event_dict(e: EventRecord) -> dict:
    return {"id": e.id, "type": e.type, "project_id": e.project_id, "task_id": e.task_id, "agent_key": e.agent_key,
            "message": e.message, "payload": e.payload, "created_at": e.created_at.isoformat() if e.created_at else None}


@router.get("/projects/{project_id}/events")
async def project_events(project_id: str, after: str | None = None, limit: int = Query(500, le=5000),
                         p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    await get_owned(s, Project, project_id, p)
    q = select(EventRecord).where(EventRecord.project_id == project_id)
    if after:
        q = q.where(EventRecord.created_at > datetime.fromisoformat(after))
    rows = (await s.execute(q.order_by(EventRecord.created_at, EventRecord.id).limit(limit))).scalars()
    return [_event_dict(e) for e in rows]


@router.get("/projects/{project_id}/replay")
async def replay(project_id: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    """Everything needed to scrub through a run: ordered events and the final task set (UI reconstructs state at time t)."""
    proj = await get_owned(s, Project, project_id, p)
    events = (await s.execute(select(EventRecord).where(EventRecord.project_id == project_id)
                              .order_by(EventRecord.created_at, EventRecord.id))).scalars()
    tasks = (await s.execute(select(Task).where(Task.project_id == project_id).order_by(Task.created_at))).scalars()
    return {"project": to_dict(proj), "events": [_event_dict(e) for e in events],
            "tasks": [to_dict(t, exclude=TASK_LIST_EXCLUDE) for t in tasks]}


async def _stream(request: Request, org_id: str, project_id: str | None):
    """DB-tail stream (durable, survives missed pub/sub messages) woken early by the in-process event bus."""
    channel = f"project:{project_id}" if project_id else f"org:{org_id}"
    wake = bus.subscribe(channel)
    last_ts: datetime | None = None
    seen: set[str] = set()
    try:
        async with session_scope() as s:
            q = select(EventRecord).where(EventRecord.org_id == org_id)
            if project_id:
                q = q.where(EventRecord.project_id == project_id)
            backlog = list((await s.execute(q.order_by(EventRecord.created_at.desc(), EventRecord.id.desc()).limit(200))).scalars())
        for e in reversed(backlog):
            seen.add(e.id)
            last_ts = e.created_at
            yield {"event": "event", "id": e.id, "data": json.dumps(_event_dict(e), default=str)}
        while not await request.is_disconnected():
            try:
                await asyncio.wait_for(wake.get(), timeout=2.0)
                while not wake.empty():
                    wake.get_nowait()
            except TimeoutError:
                yield {"event": "ping", "data": "{}"}
            async with session_scope() as s:
                q = select(EventRecord).where(EventRecord.org_id == org_id)
                if project_id:
                    q = q.where(EventRecord.project_id == project_id)
                if last_ts is not None:
                    q = q.where(EventRecord.created_at >= last_ts)
                rows = list((await s.execute(q.order_by(EventRecord.created_at, EventRecord.id).limit(500))).scalars())
            for e in rows:
                if e.id in seen:
                    continue
                seen.add(e.id)
                last_ts = e.created_at
                yield {"event": "event", "id": e.id, "data": json.dumps(_event_dict(e), default=str)}
            if len(seen) > 20_000:
                seen = {e.id for e in rows}
    finally:
        bus.unsubscribe(channel, wake)


@router.get("/projects/{project_id}/stream")
async def stream_project(project_id: str, request: Request, p: Principal = Depends(current_principal),
                         s: AsyncSession = Depends(get_session)):
    await get_owned(s, Project, project_id, p)
    await release(s)
    return EventSourceResponse(_stream(request, p.org_id, project_id))


@router.get("/events/stream")
async def stream_org(request: Request, p: Principal = Depends(current_principal)):
    return EventSourceResponse(_stream(request, p.org_id, None))


@router.get("/events")
async def org_events(limit: int = Query(100, le=1000), type: str | None = None, p: Principal = Depends(current_principal),
                     s: AsyncSession = Depends(get_session)):
    q = select(EventRecord).where(EventRecord.org_id == p.org_id)
    if type:
        q = q.where(EventRecord.type == type)
    rows = (await s.execute(q.order_by(EventRecord.created_at.desc()).limit(limit))).scalars()
    return [_event_dict(e) for e in rows]


# ------------------------------------------------------------------------------------------ tasks
@router.get("/tasks/{task_id}")
async def get_task(task_id: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    t = await get_owned(s, Task, task_id, p)
    agent = (await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == p.org_id, AgentDefinition.key == t.agent_key))).scalar_one_or_none()
    execs = list((await s.execute(select(Execution).where(Execution.task_id == t.id).order_by(Execution.created_at))).scalars())
    steps = list((await s.execute(select(ExecutionStep).where(ExecutionStep.task_id == t.id)
                                  .order_by(ExecutionStep.created_at, ExecutionStep.seq))).scalars())
    calls = (await s.execute(select(ToolCall).where(ToolCall.task_id == t.id).order_by(ToolCall.created_at))).scalars()
    evals = (await s.execute(select(Evaluation).where(Evaluation.task_id == t.id).order_by(Evaluation.created_at))).scalars()
    revs = (await s.execute(select(TaskRevision).where(TaskRevision.task_id == t.id).order_by(TaskRevision.revision))).scalars()
    usage = (await s.execute(select(ModelUsage).where(ModelUsage.task_id == t.id).order_by(ModelUsage.created_at))).scalars()
    approvals = (await s.execute(select(Approval).where(Approval.task_id == t.id).order_by(Approval.created_at))).scalars()
    siblings = list((await s.execute(select(Task).where(Task.project_id == t.project_id))).scalars())
    steps_by_exec: dict[str, list] = defaultdict(list)
    for st in steps:
        steps_by_exec[st.execution_id].append(to_dict(st))
    return {
        "task": to_dict(t),
        "agent": to_dict(agent) if agent else None,
        "executions": [to_dict(e, extra={"steps": steps_by_exec.get(e.id, [])}) for e in execs],
        "tool_calls": [to_dict(c) for c in calls],
        "evaluations": [to_dict(e) for e in evals],
        "revisions": [to_dict(r) for r in revs],
        "model_usage": [to_dict(u) for u in usage],
        "approvals": [to_dict(a) for a in approvals],
        "children": [to_dict(c, exclude=TASK_LIST_EXCLUDE) for c in siblings if c.parent_task_id == t.id],
        "dependencies": [to_dict(c, exclude=TASK_LIST_EXCLUDE) for c in siblings if c.id in (t.depends_on or [])],
        "reviews": [to_dict(c, exclude=TASK_LIST_EXCLUDE) for c in siblings if (c.input or {}).get("_review_of") == t.id],
    }


@router.post("/tasks/{task_id}/retry")
async def retry(task_id: str, p: Principal = Depends(require("project.write")), s: AsyncSession = Depends(get_session)):
    await get_owned(s, Task, task_id, p)
    await release(s)
    try:
        await orchestrator.retry_task(task_id, p.user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, p: Principal = Depends(require("project.write")), s: AsyncSession = Depends(get_session)):
    await get_owned(s, Task, task_id, p)
    await release(s)
    try:
        await orchestrator.cancel_task(task_id, p.user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


class ReassignIn(BaseModel):
    agent_key: str = Field(min_length=1, max_length=80)


@router.post("/tasks/{task_id}/reassign")
async def reassign(task_id: str, body: ReassignIn, p: Principal = Depends(require("project.write")),
                   s: AsyncSession = Depends(get_session)):
    await get_owned(s, Task, task_id, p)
    await release(s)
    try:
        await orchestrator.reassign_task(task_id, body.agent_key, p.user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.get("/tool-calls")
async def tool_calls(project_id: str | None = None, tool: str | None = None, status: str | None = None,
                     limit: int = Query(200, le=2000), p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    q = select(ToolCall).where(ToolCall.org_id == p.org_id)
    if project_id:
        q = q.where(ToolCall.project_id == project_id)
    if tool:
        q = q.where(ToolCall.tool_name == tool)
    if status:
        q = q.where(ToolCall.status == status)
    rows = (await s.execute(q.order_by(ToolCall.created_at.desc()).limit(limit))).scalars()
    return [to_dict(c) for c in rows]
