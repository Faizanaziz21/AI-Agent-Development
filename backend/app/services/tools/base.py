from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel


class PermissionLevel:
    READ = "read"  # no side effects
    WRITE = "write"  # internal state changes (CRM, files, tickets)
    EXTERNAL = "external"  # leaves the organization boundary (email, Slack, HTTP)
    PRIVILEGED = "privileged"  # destructive / financial / code execution

    ORDER = [READ, WRITE, EXTERNAL, PRIVILEGED]


class ToolError(Exception):
    code = "tool_error"
    transient = False


class ToolTimeout(ToolError):
    code = "timeout"
    transient = True


class ToolTransientError(ToolError):
    code = "transient"
    transient = True


class ToolInvalidResponse(ToolError):
    code = "invalid_response"
    transient = True


class ToolValidationError(ToolError):
    code = "validation"


class ToolPermissionDenied(ToolError):
    code = "permission_denied"


class ToolApprovalRequired(ToolError):
    code = "approval_required"

    def __init__(self, approval_id: str, title: str):
        super().__init__(f"approval required: {title}")
        self.approval_id = approval_id
        self.title = title


@dataclass
class ToolContext:
    org_id: str
    agent_key: str
    project_id: str | None = None
    task_id: str | None = None
    execution_id: str | None = None
    chaos: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    # strings that originated in untrusted content during this execution (taint tracking)
    tainted_values: set[str] = field(default_factory=set)
    approved_payload: dict[str, Any] | None = None
    qa_passed_capabilities: set[str] = field(default_factory=set)


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str] = "general"
    permission_level: ClassVar[str] = PermissionLevel.READ
    input_model: ClassVar[type[BaseModel]]
    output_description: ClassVar[dict[str, Any]] = {"type": "object"}
    timeout_seconds: ClassVar[float] = 15.0
    retry_policy: ClassVar[dict[str, Any]] = {"max_attempts": 3, "backoff_seconds": 0.2, "backoff_multiplier": 2.0}
    requires_approval: ClassVar[bool] = False
    # argument paths whose values must not come from untrusted content (e.g. email recipients)
    sensitive_args: ClassVar[tuple[str, ...]] = ()
    # output contains free text from outside the trust boundary (web pages, documents, API bodies)
    untrusted_output: ClassVar[bool] = False

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        return cls.input_model.model_json_schema()

    def facts(self, args: BaseModel) -> dict[str, Any]:
        """Derived facts exposed to the policy engine (e.g. recipient_count, amount_usd)."""
        return {}

    def approval_request(self, args: BaseModel) -> tuple[str, dict[str, Any]]:
        return f"{self.name} requested", args.model_dump()

    @abstractmethod
    async def run(self, ctx: ToolContext, args: BaseModel) -> dict[str, Any]: ...

    def validate_output(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ToolInvalidResponse(f"{self.name} returned {type(result).__name__}, expected object")
        return result


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def spec(self, name: str) -> dict[str, Any]:
        t = self._tools[name]
        return {"name": t.name, "description": t.description, "input_schema": t.input_schema(),
                "permission_level": t.permission_level}


registry = ToolRegistry()


def register(cls: type[Tool]) -> type[Tool]:
    registry.register(cls())
    return cls
