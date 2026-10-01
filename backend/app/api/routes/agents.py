from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_principal, get_owned, require, to_dict
from app.core.db import get_session
from app.core.security import ROLES, Principal
from app.models import AgentDefinition, Policy, Task, ToolCall, ToolDefinition
from app.services import audit
from app.services.model_gateway.catalog import BY_NAME, TIERS
from app.services.policy.engine import validate_condition
from app.services.tools.base import registry

router = APIRouter(tags=["workforce"])


class AgentIn(BaseModel):
    key: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=4000)
    system_instructions: str = Field(default="", max_length=20000)
    allowed_tools: list[str] = Field(default_factory=list)
    prohibited_tools: list[str] = Field(default_factory=list)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    max_iterations: int = Field(default=8, ge=1, le=50)
    token_budget: int = Field(default=60000, ge=1000, le=5_000_000)
    cost_budget_usd: float = Field(default=2.0, ge=0, le=10_000)
    memory_config: dict[str, Any] = Field(default_factory=lambda: {"session": True, "long_term": True, "semantic_top_k": 3})
    escalation_rules: dict[str, Any] = Field(default_factory=lambda: {"on_failure": "human"})
    approval_rules: list[dict[str, Any]] = Field(default_factory=list)
    can_delegate_to: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    system_instructions: str | None = Field(default=None, max_length=20000)
    allowed_tools: list[str] | None = None
    prohibited_tools: list[str] | None = None
    model_policy: dict[str, Any] | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=50)
    token_budget: int | None = Field(default=None, ge=1000, le=5_000_000)
    cost_budget_usd: float | None = Field(default=None, ge=0, le=10_000)
    memory_config: dict[str, Any] | None = None
    escalation_rules: dict[str, Any] | None = None
    approval_rules: list[dict[str, Any]] | None = None
    can_delegate_to: list[str] | None = None
    capabilities: list[str] | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool | None = None


async def _validate_agent(s: AsyncSession, org_id: str, data: dict[str, Any], self_key: str | None) -> None:
    known_tools = {t.name for t in registry.all()}
    for field in ("allowed_tools", "prohibited_tools"):
        bad = [t for t in data.get(field) or [] if t != "*" and t not in known_tools]
        if bad:
            raise HTTPException(422, f"{field}: unknown tools {bad}")
    mp = data.get("model_policy") or {}
    if mp.get("tier") and mp["tier"] not in TIERS:
        raise HTTPException(422, f"model_policy.tier must be one of {TIERS}")
    if mp.get("model") and mp["model"] not in BY_NAME:
        raise HTTPException(422, f"model_policy.model '{mp['model']}' is not in the model catalog")
    for rule in data.get("approval_rules") or []:
        if rule.get("tool") not in known_tools:
            raise HTTPException(422, f"approval rule references unknown tool {rule.get('tool')!r}")
        if rule.get("role", "approver") not in ROLES:
            raise HTTPException(422, f"approval rule role must be one of {ROLES}")
    targets = data.get("can_delegate_to") or []
    if targets:
        if self_key and self_key in targets:
            raise HTTPException(422, "an agent cannot delegate to itself")
        existing = set((await s.execute(select(AgentDefinition.key).where(AgentDefinition.org_id == org_id,
                                                                         AgentDefinition.key.in_(targets)))).scalars())
        missing = sorted(set(targets) - existing)
        if missing:
            raise HTTPException(422, f"can_delegate_to: unknown agents {missing}")
    alt = (data.get("escalation_rules") or {}).get("alternate_agent")
    if alt and not (await s.execute(select(AgentDefinition.id).where(AgentDefinition.org_id == org_id, AgentDefinition.key == alt))).first():
        raise HTTPException(422, f"escalation_rules.alternate_agent '{alt}' does not exist")


@router.get("/agents")
async def list_agents(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    agents = list((await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == p.org_id)
                                   .order_by(AgentDefinition.is_builtin.desc(), AgentDefinition.name))).scalars())
    stats = {k: (n, c, q) for k, n, c, q in (await s.execute(
        select(Task.agent_key, func.count(), func.sum(Task.spent_usd), func.avg(Task.quality_score))
        .where(Task.org_id == p.org_id).group_by(Task.agent_key))).all()}
    out = []
    for a in agents:
        n, cost, q = stats.get(a.key, (0, 0.0, None))
        out.append(to_dict(a, extra={"stats": {"tasks": n, "cost_usd": round(cost or 0, 4),
                                               "avg_quality": round(q, 3) if q is not None else None}}))
    return out


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    a = await get_owned(s, AgentDefinition, agent_id, p)
    recent = (await s.execute(select(Task).where(Task.org_id == p.org_id, Task.agent_key == a.key)
                              .order_by(Task.created_at.desc()).limit(20))).scalars()
    return to_dict(a, extra={"recent_tasks": [to_dict(t, exclude={"input", "output", "checkpoint"}) for t in recent]})


@router.post("/agents", status_code=201)
async def create_agent(body: AgentIn, p: Principal = Depends(require("agent.write")), s: AsyncSession = Depends(get_session)):
    if (await s.execute(select(AgentDefinition.id).where(AgentDefinition.org_id == p.org_id, AgentDefinition.key == body.key))).first():
        raise HTTPException(409, f"agent key '{body.key}' already exists")
    data = body.model_dump()
    await _validate_agent(s, p.org_id, data, body.key)
    a = AgentDefinition(org_id=p.org_id, is_builtin=False, **data)
    s.add(a)
    await s.flush()
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="agent.created", resource_type="agent",
                       resource_id=a.id, details={"key": a.key, "allowed_tools": a.allowed_tools})
    return to_dict(a)


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate, p: Principal = Depends(require("agent.write")),
                       s: AsyncSession = Depends(get_session)):
    a = await get_owned(s, AgentDefinition, agent_id, p)
    changes = body.model_dump(exclude_none=True)
    await _validate_agent(s, p.org_id, changes, a.key)
    for k, v in changes.items():
        setattr(a, k, v)
    a.version += 1
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="agent.updated", resource_type="agent",
                       resource_id=a.id, details={"key": a.key, "fields": sorted(changes), "version": a.version})
    return to_dict(a)


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, p: Principal = Depends(require("agent.write")), s: AsyncSession = Depends(get_session)):
    a = await get_owned(s, AgentDefinition, agent_id, p)
    if a.is_builtin:
        raise HTTPException(400, "built-in agents can be disabled but not deleted")
    await s.delete(a)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="agent.deleted", resource_type="agent",
                       resource_id=agent_id, details={"key": a.key})


@router.get("/tools")
async def list_tools(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    defs = list((await s.execute(select(ToolDefinition).where(ToolDefinition.org_id == p.org_id)
                                 .order_by(ToolDefinition.category, ToolDefinition.name))).scalars())
    stats = {(n, st): (c, d) for n, st, c, d in (await s.execute(
        select(ToolCall.tool_name, ToolCall.status, func.count(), func.avg(ToolCall.duration_ms))
        .where(ToolCall.org_id == p.org_id).group_by(ToolCall.tool_name, ToolCall.status))).all()}
    agents = list((await s.execute(select(AgentDefinition.key, AgentDefinition.allowed_tools, AgentDefinition.prohibited_tools)
                                   .where(AgentDefinition.org_id == p.org_id))).all())
    out = []
    for t in defs:
        impl = registry.get(t.name)
        calls = {st: c for (n, st), (c, _d) in stats.items() if n == t.name}
        total = sum(calls.values())
        lat = [d * c for (n, _st), (c, d) in stats.items() if n == t.name and d is not None]
        out.append(to_dict(t, extra={
            "untrusted_output": bool(impl and impl.untrusted_output),
            "sensitive_args": list(impl.sensitive_args) if impl else [],
            "agents": [k for k, allowed, prohibited in agents if (t.name in (allowed or []) or "*" in (allowed or []))
                       and t.name not in (prohibited or [])],
            "stats": {"calls": total, "by_status": calls, "avg_ms": round(sum(lat) / total) if total else None},
        }))
    return out


class ToolUpdate(BaseModel):
    enabled: bool | None = None
    requires_approval: bool | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    retry_policy: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


@router.patch("/tools/{tool_id}")
async def update_tool(tool_id: str, body: ToolUpdate, p: Principal = Depends(require("tool.write")),
                      s: AsyncSession = Depends(get_session)):
    t = await get_owned(s, ToolDefinition, tool_id, p)
    changes = body.model_dump(exclude_none=True)
    impl = registry.get(t.name)
    if body.requires_approval is False and impl and impl.requires_approval:
        raise HTTPException(400, f"{t.name} always requires approval; its implementation enforces this")
    if body.retry_policy is not None:
        mx = body.retry_policy.get("max_attempts", 1)
        if not isinstance(mx, int) or not 1 <= mx <= 10:
            raise HTTPException(422, "retry_policy.max_attempts must be an integer between 1 and 10")
    for k, v in changes.items():
        setattr(t, k, v)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="tool.updated", resource_type="tool",
                       resource_id=t.id, details={"name": t.name, **changes})
    return to_dict(t)


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    scope: Literal["tool_call", "knowledge_access", "delegation"] = "tool_call"
    condition: dict[str, Any]
    effect: Literal["deny", "require_approval", "require_qa", "allow"]
    required_role: str = "approver"
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True


def _check_policy(body: PolicyIn) -> None:
    try:
        validate_condition(body.condition)
    except ValueError as exc:
        raise HTTPException(422, f"invalid condition: {exc}") from exc
    if body.required_role not in ROLES:
        raise HTTPException(422, f"required_role must be one of {ROLES}")


@router.get("/policies")
async def list_policies(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Policy).where(Policy.org_id == p.org_id).order_by(Policy.priority, Policy.name))).scalars()
    return [to_dict(x) for x in rows]


@router.post("/policies", status_code=201)
async def create_policy(body: PolicyIn, p: Principal = Depends(require("policy.write")), s: AsyncSession = Depends(get_session)):
    _check_policy(body)
    row = Policy(org_id=p.org_id, **body.model_dump())
    s.add(row)
    await s.flush()
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="policy.created", resource_type="policy",
                       resource_id=row.id, details=body.model_dump())
    return to_dict(row)


@router.put("/policies/{policy_id}")
async def update_policy(policy_id: str, body: PolicyIn, p: Principal = Depends(require("policy.write")),
                        s: AsyncSession = Depends(get_session)):
    _check_policy(body)
    row = await get_owned(s, Policy, policy_id, p)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="policy.updated", resource_type="policy",
                       resource_id=row.id, details=body.model_dump())
    return to_dict(row)


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_policy(policy_id: str, p: Principal = Depends(require("policy.write")), s: AsyncSession = Depends(get_session)):
    row = await get_owned(s, Policy, policy_id, p)
    await s.delete(row)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="policy.deleted", resource_type="policy",
                       resource_id=policy_id, details={"name": row.name})


class PolicyTest(BaseModel):
    scope: Literal["tool_call", "knowledge_access", "delegation"] = "tool_call"
    context: dict[str, Any]


@router.post("/policies/simulate")
async def simulate_policy(body: PolicyTest, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    from app.services.policy.engine import decide, load_policies

    return decide(await load_policies(s, p.org_id), body.scope, body.context).as_dict()
