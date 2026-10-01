"""Guardrail / policy engine.

Rules are data (`policies` table), evaluated on every tool call (scope=tool_call), knowledge
access (scope=knowledge_access) and delegation (scope=delegation). Prompts may *describe* rules
to agents, but enforcement happens here.

Condition DSL:
    {"all": [cond, ...]} | {"any": [cond, ...]} | {"not": cond}
    {"field": "facts.recipient_count", "op": "gt", "value": 50}
ops: eq ne gt gte lt lte in not_in contains exists matches
Effects (precedence high→low): deny > require_qa > require_approval > allow
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telemetry import POLICY_DECISIONS
from app.models import Policy

EFFECT_RANK = {"allow": 0, "require_approval": 1, "require_qa": 2, "deny": 3}
_MISSING = object()


def _get(ctx: dict, path: str) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _cmp(op: str, actual: Any, expected: Any) -> bool:
    if op == "exists":
        return (actual is not _MISSING) == bool(expected if expected is not None else True)
    if actual is _MISSING:
        return False
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gt":
            return float(actual) > float(expected)
        if op == "gte":
            return float(actual) >= float(expected)
        if op == "lt":
            return float(actual) < float(expected)
        if op == "lte":
            return float(actual) <= float(expected)
        if op == "in":
            return actual in expected
        if op == "not_in":
            return actual not in expected
        if op == "contains":
            return expected in actual
        if op == "matches":
            return re.search(str(expected), str(actual), re.I) is not None
    except (TypeError, ValueError):
        return False
    raise ValueError(f"unknown operator {op}")


def evaluate_condition(cond: dict, ctx: dict) -> bool:
    if not cond:
        return True
    if "all" in cond:
        return all(evaluate_condition(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(evaluate_condition(c, ctx) for c in cond["any"])
    if "not" in cond:
        return not evaluate_condition(cond["not"], ctx)
    return _cmp(cond.get("op", "eq"), _get(ctx, cond["field"]), cond.get("value"))


def validate_condition(cond: dict) -> None:
    if not isinstance(cond, dict):
        raise ValueError("condition must be an object")
    if not cond:
        return
    for k in ("all", "any"):
        if k in cond:
            if not isinstance(cond[k], list):
                raise ValueError(f"'{k}' must be a list")
            for c in cond[k]:
                validate_condition(c)
            return
    if "not" in cond:
        validate_condition(cond["not"])
        return
    if "field" not in cond:
        raise ValueError("leaf condition requires 'field'")
    if cond.get("op", "eq") not in {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "exists", "matches"}:
        raise ValueError(f"unknown operator {cond.get('op')}")


@dataclass
class PolicyDecision:
    effect: str = "allow"
    required_role: str = "approver"
    matched: list[dict] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [f"{m['name']}: {m['description']}" for m in self.matched]

    def as_dict(self) -> dict:
        return {"effect": self.effect, "required_role": self.required_role, "matched": self.matched}


def decide(policies: list[dict], scope: str, ctx: dict) -> PolicyDecision:
    decision = PolicyDecision()
    for p in sorted(policies, key=lambda p: p.get("priority", 100)):
        if not p.get("enabled", True) or p.get("scope", "tool_call") != scope:
            continue
        if not evaluate_condition(p.get("condition") or {}, ctx):
            continue
        effect = p["effect"]
        if effect == "require_qa" and ctx.get("qa_passed"):
            continue
        decision.matched.append({"id": p.get("id"), "name": p["name"], "effect": effect,
                                 "description": p.get("description", "")})
        if EFFECT_RANK[effect] > EFFECT_RANK[decision.effect]:
            decision.effect = effect
            decision.required_role = p.get("required_role") or "approver"
        elif effect == decision.effect == "require_approval" and p.get("required_role") not in (None, "approver"):
            decision.required_role = p["required_role"]
    POLICY_DECISIONS.labels(decision.effect).inc()
    return decision


async def load_policies(session: AsyncSession, org_id: str) -> list[dict]:
    rows = (await session.execute(select(Policy).where(Policy.org_id == org_id, Policy.enabled.is_(True)))).scalars()
    return [
        {"id": p.id, "name": p.name, "description": p.description, "scope": p.scope, "condition": p.condition,
         "effect": p.effect, "required_role": p.required_role, "priority": p.priority, "enabled": p.enabled}
        for p in rows
    ]
