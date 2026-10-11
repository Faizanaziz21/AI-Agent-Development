"""Planning Service: Supervisor agent produces a plan; we validate it and materialise the task DAG."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.events import EventType, emit
from app.models import AgentDefinition, Objective, Plan, Project, ProjectStatus, Task, TaskStatus
from app.services import audit
from app.services.agents.decision import MalformedOutput, parse_json_object
from app.services.agents.profile import AgentProfile
from app.services.model_gateway.gateway import ModelGateway, get_gateway
from app.services.model_gateway.local_engine import CONTEXT_MARKER
from app.services.model_gateway.types import CallContext, ModelRequest
from app.services.planning.dag import PlanValidationError, levels, validate_plan
from app.services.security.injection import SYSTEM_GUARD

PLAN_SCHEMA = {
    "summary": "string", "strategy": "object",
    "tasks": [{"key": "slug", "title": "string", "agent": "agent key", "capability": "skill id",
               "depends_on": ["task keys"], "input": "object", "requires_review": "bool",
               "review_criteria": {"type": "outreach|scoring|support_answer|report", "threshold": "0..1"},
               "budget_usd": "number", "priority": "1..10"}],
}


class PlanningFailed(Exception):
    pass


async def create_plan(project_id: str, gateway: ModelGateway | None = None, max_attempts: int = 3) -> Plan:
    gw = gateway or get_gateway()
    s_ = get_settings()
    async with session_scope() as s:
        project = await s.get(Project, project_id)
        objective = (await s.execute(select(Objective).where(Objective.project_id == project_id)
                                     .order_by(Objective.created_at.desc()).limit(1))).scalar_one()
        agents = list((await s.execute(select(AgentDefinition).where(
            AgentDefinition.org_id == project.org_id, AgentDefinition.enabled.is_(True)))).scalars())
        supervisor = next((AgentProfile.from_row(a) for a in agents if a.key == "supervisor"), None)
        org_id, chaos, template = project.org_id, dict(project.chaos or {}), project.template
        params = dict(objective.parameters or {})
        if template and template != "generic":
            params.setdefault("template", template)
        context = {"mode": "planning", "objective": {"text": objective.text, "parameters": params},
                   "agents": [{"key": a.key, "role": a.role, "description": a.description, "capabilities": a.capabilities} for a in agents],
                   "constraints": {"max_tasks": s_.max_tasks_per_project, "max_parallel_research_agents": 8}}
        objective_id = objective.id
    known = {a.key for a in agents}
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        if last_err:
            context["previous_error"] = last_err
        system = "\n\n".join([SYSTEM_GUARD.replace("DECISION_SCHEMA", "PLAN_SCHEMA"),
                              f"ROLE: Supervisor Agent — planning.\n{supervisor.system_instructions if supervisor else ''}",
                              "PLAN_SCHEMA: " + json.dumps(PLAN_SCHEMA)])
        req = ModelRequest(messages=[{"role": "system", "content": system},
                                     {"role": "user", "content": f"{CONTEXT_MARKER}\n{json.dumps(context, default=str)}"}],
                           purpose="planning", agent_key="supervisor", max_output_tokens=4096)
        resp = await gw.complete(req, CallContext(org_id=org_id, project_id=project_id, agent_key="supervisor", chaos=chaos))
        try:
            spec = validate_plan(parse_json_object(resp.content), known, s_.max_tasks_per_project)
            break
        except (MalformedOutput, PlanValidationError) as exc:
            last_err = str(exc)
            async with session_scope() as s:
                emit(s, org_id, EventType.AGENT_STEP, project_id=project_id, agent_key="supervisor",
                     message=f"Plan attempt {attempt} rejected by validator: {last_err}", payload={"phase": "EVALUATE"})
    else:
        raise PlanningFailed(f"supervisor could not produce a valid plan: {last_err}")

    lvl = levels(spec.tasks)
    async with session_scope() as s:
        project = await s.get(Project, project_id)
        version = len((await s.execute(select(Plan.id).where(Plan.project_id == project_id))).all()) + 1
        plan = Plan(org_id=org_id, project_id=project_id, objective_id=objective_id, version=version, summary=spec.summary,
                    strategy=spec.strategy, graph={"levels": lvl, "edges": [[d, t.key] for t in spec.tasks for d in t.depends_on]})
        s.add(plan)
        await s.flush()
        ids: dict[str, str] = {}
        rows: list[Task] = []
        for pt in spec.tasks:
            row = Task(org_id=org_id, project_id=project_id, plan_id=plan.id, key=pt.key, title=pt.title,
                       description=pt.description, capability=pt.capability, agent_key=pt.agent,
                       agent_instance=pt.instance or pt.agent.replace("_", " ").title(), input=pt.input,
                       requires_review=pt.requires_review, review_criteria=pt.review_criteria, budget_usd=pt.budget_usd,
                       priority=pt.priority, status=TaskStatus.PENDING, attempt=1)
            s.add(row)
            rows.append(row)
        await s.flush()
        for row in rows:
            ids[row.key] = row.id
        for row, pt in zip(rows, spec.tasks, strict=True):
            row.depends_on = [ids[d] for d in pt.depends_on]
            emit(s, org_id, EventType.TASK_CREATED, project_id=project_id, task_id=row.id, agent_key=row.agent_key,
                 message=f"Task '{row.title}' created (level {lvl[row.key]})", payload={"key": row.key, "depends_on": pt.depends_on})
            emit(s, org_id, EventType.TASK_ASSIGNED, project_id=project_id, task_id=row.id, agent_key=row.agent_key,
                 message=f"'{row.title}' assigned to {row.agent_instance}", payload={"agent": row.agent_key})
        project.status = ProjectStatus.RUNNING
        emit(s, org_id, EventType.PLAN_CREATED, project_id=project_id, agent_key="supervisor",
             message=f"Supervisor created plan v{version}: {len(rows)} tasks across {max(lvl.values()) + 1} stages",
             payload={"plan_id": plan.id, "tasks": len(rows), "summary": spec.summary})
        await audit.record(s, org_id, actor_type="agent", actor_id="supervisor", action="plan.created", resource_type="plan",
                           resource_id=plan.id, project_id=project_id, details={"tasks": len(rows), "version": version})
    return plan
