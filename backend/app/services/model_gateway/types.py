from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Base for provider failures. `transient` errors are eligible for fallback/retry."""

    transient = True


class ProviderUnavailable(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    pass


class ProviderBadRequest(ProviderError):
    transient = False


class BudgetExceeded(Exception):
    def __init__(self, scope: str, limit: float, spent: float):
        super().__init__(f"{scope} budget exceeded: spent ${spent:.4f} of ${limit:.2f}")
        self.scope, self.limit, self.spent = scope, limit, spent


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    tier: str  # economy | standard | advanced
    context_window: int
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    vision: bool = False
    long_context: bool = False
    general: bool = True  # False for specialised models only chosen for long-context / vision requests

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_cost_per_mtok + output_tokens * self.output_cost_per_mtok) / 1_000_000


@dataclass
class ModelRequest:
    messages: list[dict[str, str]]
    purpose: str = "reasoning"  # planning|reasoning|extraction|classification|evaluation|synthesis|long_context|vision
    agent_key: str = ""
    tier: str | None = None
    pinned_model: str | None = None
    response_format: str = "json"
    max_output_tokens: int = 2048
    images: list[str] = field(default_factory=list)
    temperature: float = 0.2


@dataclass
class ModelResponse:
    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float = 0.0
    fallback_from: str | None = None
    downgraded: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallContext:
    """Who is calling and under which budget/chaos profile."""

    org_id: str
    project_id: str | None = None
    task_id: str | None = None
    agent_key: str = ""
    chaos: dict[str, Any] = field(default_factory=dict)
    downgrade: bool = False


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
