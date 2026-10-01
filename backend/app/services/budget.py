"""Cost control: organization, project, agent and task budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from app.core.db import session_scope
from app.models import AgentDefinition, ModelUsage, Organization, Project, Task
from app.services.model_gateway.types import CallContext, ModelRequest, ModelResponse, ModelSpec

WARN_RATIO = 0.8


@dataclass
class BudgetState:
    ratios: dict[str, float] = field(default_factory=dict)
    limits: dict[str, float] = field(default_factory=dict)
    spent: dict[str, float] = field(default_factory=dict)

    @property
    def worst_scope(self) -> str | None:
        return max(self.ratios, key=self.ratios.get) if self.ratios else None

    @property
    def worst_ratio(self) -> float:
        return max(self.ratios.values()) if self.ratios else 0.0

    @property
    def exceeded(self) -> bool:
        return self.worst_ratio >= 1.0

    @property
    def downgrade(self) -> bool:
        return self.worst_ratio >= WARN_RATIO

    def as_dict(self) -> dict:
        return {"ratios": {k: round(v, 3) for k, v in self.ratios.items()}, "limits": self.limits,
                "spent": {k: round(v, 4) for k, v in self.spent.items()}, "downgrade": self.downgrade,
                "exceeded": self.exceeded, "worst_scope": self.worst_scope}


def month_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def check_budgets(session, org_id: str, project_id: str | None, task_id: str | None, agent_key: str,
                        task_override_limit: float | None = None) -> BudgetState:
    st = BudgetState()
    org = await session.get(Organization, org_id)
    if org and org.monthly_budget_usd > 0:
        spent = (await session.execute(
            select(func.coalesce(func.sum(ModelUsage.cost_usd), 0.0)).where(
                ModelUsage.org_id == org_id, ModelUsage.created_at >= month_start())
        )).scalar_one()
        st.limits["organization"], st.spent["organization"] = org.monthly_budget_usd, float(spent)
        st.ratios["organization"] = float(spent) / org.monthly_budget_usd
    if project_id:
        proj = await session.get(Project, project_id)
        if proj and proj.org_id == org_id and proj.budget_usd > 0:
            st.limits["project"], st.spent["project"] = proj.budget_usd, proj.spent_usd
            st.ratios["project"] = proj.spent_usd / proj.budget_usd
        agent = (await session.execute(
            select(AgentDefinition).where(AgentDefinition.org_id == org_id, AgentDefinition.key == agent_key)
        )).scalar_one_or_none()
        if agent and agent.cost_budget_usd > 0:
            spent = (await session.execute(
                select(func.coalesce(func.sum(Task.spent_usd), 0.0)).where(
                    Task.org_id == org_id, Task.project_id == project_id, Task.agent_key == agent_key)
            )).scalar_one()
            st.limits["agent"], st.spent["agent"] = agent.cost_budget_usd, float(spent)
            st.ratios["agent"] = float(spent) / agent.cost_budget_usd
    if task_id:
        task = await session.get(Task, task_id)
        if task and task.org_id == org_id:
            limit = task_override_limit or task.budget_usd
            if limit > 0:
                st.limits["task"], st.spent["task"] = limit, task.spent_usd
                st.ratios["task"] = task.spent_usd / limit
    return st


async def record_model_usage(ctx: CallContext, req: ModelRequest, spec: ModelSpec, resp: ModelResponse | None,
                             error: str | None, fallback_from: str | None) -> None:
    cost = 0.0 if error or resp is None else spec.cost(resp.input_tokens, resp.output_tokens)
    tokens = 0 if resp is None else resp.input_tokens + resp.output_tokens
    async with session_scope() as s:
        s.add(ModelUsage(
            org_id=ctx.org_id, project_id=ctx.project_id, task_id=ctx.task_id, agent_key=ctx.agent_key,
            provider=spec.provider, model=spec.name, purpose=req.purpose,
            latency_ms=resp.latency_ms if resp else 0,
            input_tokens=resp.input_tokens if resp and not error else 0,
            output_tokens=resp.output_tokens if resp and not error else 0,
            cost_usd=cost, success=error is None, error=error, fallback_from=fallback_from, downgraded=ctx.downgrade,
        ))
        if error is None:
            if ctx.task_id:
                await s.execute(update(Task).where(Task.id == ctx.task_id).values(
                    spent_usd=Task.spent_usd + cost, tokens_used=Task.tokens_used + tokens))
            if ctx.project_id:
                await s.execute(update(Project).where(Project.id == ctx.project_id).values(
                    spent_usd=Project.spent_usd + cost, tokens_used=Project.tokens_used + tokens))
