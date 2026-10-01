from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select

from app.core.db import session_scope
from app.models import Objective, Plan, Project, ProjectStatus, Task
from app.services import orchestrator


async def make_project(org_id: str, tasks: list[dict[str, Any]], *, chaos: dict | None = None, budget_usd: float = 20.0,
                       name: str = "test project") -> tuple[str, dict[str, str]]:
    """Create a running project with an explicit task DAG (bypassing the planner) and schedule it.

    Each task dict: key, agent, capability, [depends_on: keys], [input], [budget_usd], [delegation_depth], [requires_review]."""
    async with session_scope() as s:
        p = Project(org_id=org_id, name=name, template="generic", status=ProjectStatus.RUNNING, budget_usd=budget_usd, chaos=chaos or {})
        s.add(p)
        await s.flush()
        obj = Objective(org_id=org_id, project_id=p.id, text=f"{name} objective", parameters={})
        s.add(obj)
        await s.flush()
        plan = Plan(org_id=org_id, project_id=p.id, objective_id=obj.id, summary="test plan")
        s.add(plan)
        await s.flush()
        ids: dict[str, str] = {}
        for spec in tasks:
            t = Task(org_id=org_id, project_id=p.id, plan_id=plan.id, key=spec["key"], title=spec.get("title", f"Task {spec['key']}"),
                     capability=spec["capability"], agent_key=spec["agent"], input=spec.get("input", {}), attempt=1,
                     depends_on=[ids[d] for d in spec.get("depends_on", [])], budget_usd=spec.get("budget_usd", 2.0),
                     delegation_depth=spec.get("delegation_depth", 0), requires_review=spec.get("requires_review", False),
                     review_criteria=spec.get("review_criteria", {}))
            s.add(t)
            await s.flush()
            ids[spec["key"]] = t.id
        pid = p.id
    await orchestrator.schedule(pid)
    return pid, ids


async def task(task_id: str) -> Task:
    async with session_scope() as s:
        return await s.get(Task, task_id)


async def tasks_of(project_id: str) -> list[Task]:
    async with session_scope() as s:
        return list((await s.execute(select(Task).where(Task.project_id == project_id).order_by(Task.created_at))).scalars())


async def wait_task(task_id: str, statuses: set[str], timeout: float = 30) -> Task:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = await task(task_id)
        if t.status in statuses:
            return t
        await asyncio.sleep(0.1)
    raise AssertionError(f"task {task_id} is {t.status}, expected one of {statuses}")


def final(output: dict, summary: str = "done") -> dict:
    return {"action": "final", "output": output, "output_summary": summary, "reason_summary": "test script", "confidence": 0.9}


def tool(name: str, args: dict) -> dict:
    return {"action": "tool_call", "tool": name, "arguments": args, "reason_summary": "test script"}


def delegate(*subs: dict) -> dict:
    return {"action": "delegate", "subtasks": list(subs), "reason_summary": "split work"}
