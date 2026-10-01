"""Plan schema and DAG validation."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class PlanValidationError(ValueError):
    pass


class PlannedTask(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_.\-]{1,80}$")
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=4000)
    agent: str = Field(min_length=2, max_length=80)
    capability: str = Field(default="generic", max_length=80)
    depends_on: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    requires_review: bool = False
    review_criteria: dict[str, Any] = Field(default_factory=dict)
    budget_usd: float = Field(default=2.0, gt=0, le=1000)
    priority: int = Field(default=5, ge=1, le=10)
    instance: str = ""


class PlanSpec(BaseModel):
    summary: str = Field(min_length=3, max_length=4000)
    strategy: dict[str, Any] = Field(default_factory=dict)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=200)


def topological_order(tasks: list[PlannedTask]) -> list[str]:
    indeg = {t.key: 0 for t in tasks}
    children: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        for d in t.depends_on:
            indeg[t.key] += 1
            children[d].append(t.key)
    q = deque(sorted(k for k, v in indeg.items() if v == 0))
    order = []
    while q:
        k = q.popleft()
        order.append(k)
        for c in children[k]:
            indeg[c] -= 1
            if indeg[c] == 0:
                q.append(c)
    if len(order) != len(tasks):
        cyclic = sorted(k for k, v in indeg.items() if v > 0)
        raise PlanValidationError(f"plan contains a dependency cycle involving: {', '.join(cyclic)}")
    return order


def levels(tasks: list[PlannedTask]) -> dict[str, int]:
    by_key = {t.key: t for t in tasks}
    lvl: dict[str, int] = {}
    for k in topological_order(tasks):
        deps = by_key[k].depends_on
        lvl[k] = 0 if not deps else 1 + max(lvl[d] for d in deps)
    return lvl


def validate_plan(raw: dict[str, Any], known_agents: set[str], max_tasks: int) -> PlanSpec:
    try:
        plan = PlanSpec.model_validate(raw)
    except ValidationError as exc:
        raise PlanValidationError("plan schema violation: " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])) from exc
    if len(plan.tasks) > max_tasks:
        raise PlanValidationError(f"plan has {len(plan.tasks)} tasks; limit is {max_tasks}")
    keys = [t.key for t in plan.tasks]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise PlanValidationError(f"duplicate task keys: {sorted(dupes)}")
    keyset = set(keys)
    for t in plan.tasks:
        missing = [d for d in t.depends_on if d not in keyset]
        if missing:
            raise PlanValidationError(f"task '{t.key}' depends on unknown tasks {missing}")
        if t.key in t.depends_on:
            raise PlanValidationError(f"task '{t.key}' depends on itself")
        if t.agent not in known_agents:
            raise PlanValidationError(f"task '{t.key}' assigned to unknown or disabled agent '{t.agent}'")
    topological_order(plan.tasks)
    return plan
