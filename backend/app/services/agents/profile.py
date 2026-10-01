from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import AgentDefinition


@dataclass(frozen=True)
class AgentProfile:
    """Immutable snapshot of an AgentDefinition used by the runtime (safe across sessions)."""

    id: str
    org_id: str
    key: str
    name: str
    role: str
    description: str
    system_instructions: str
    allowed_tools: tuple[str, ...]
    prohibited_tools: tuple[str, ...]
    model_policy: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 8
    token_budget: int = 60000
    cost_budget_usd: float = 2.0
    memory_config: dict[str, Any] = field(default_factory=dict)
    escalation_rules: dict[str, Any] = field(default_factory=dict)
    approval_rules: tuple[dict, ...] = ()
    can_delegate_to: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, a: AgentDefinition) -> AgentProfile:
        return cls(
            id=a.id, org_id=a.org_id, key=a.key, name=a.name, role=a.role, description=a.description,
            system_instructions=a.system_instructions, allowed_tools=tuple(a.allowed_tools or ()),
            prohibited_tools=tuple(a.prohibited_tools or ()), model_policy=dict(a.model_policy or {}),
            max_iterations=a.max_iterations, token_budget=a.token_budget, cost_budget_usd=a.cost_budget_usd,
            memory_config=dict(a.memory_config or {}), escalation_rules=dict(a.escalation_rules or {}),
            approval_rules=tuple(a.approval_rules or ()), can_delegate_to=tuple(a.can_delegate_to or ()),
            capabilities=tuple(a.capabilities or ()),
        )

    def may_use(self, tool: str) -> bool:
        if tool in self.prohibited_tools:
            return False
        return "*" in self.allowed_tools or tool in self.allowed_tools
