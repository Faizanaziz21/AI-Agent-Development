"""Builds the prompt for one agent iteration. Only safe, structured context goes in."""

from __future__ import annotations

import json
from typing import Any

from app.services.agents.decision import DECISION_SCHEMA
from app.services.agents.profile import AgentProfile
from app.services.model_gateway.local_engine import CONTEXT_MARKER
from app.services.security.injection import SYSTEM_GUARD
from app.services.tools.base import registry


def tool_specs(agent: AgentProfile, enabled: set[str]) -> list[dict[str, Any]]:
    out = []
    for t in registry.all():
        if t.name in enabled and agent.may_use(t.name):
            out.append({"name": t.name, "description": t.description, "permission_level": t.permission_level,
                        "input_schema": t.input_schema()})
    return out


def system_prompt(agent: AgentProfile, tools: list[dict[str, Any]]) -> str:
    return "\n\n".join([
        SYSTEM_GUARD,
        f"ROLE: {agent.name} ({agent.role})\n{agent.description}",
        f"INSTRUCTIONS:\n{agent.system_instructions}",
        "LIFECYCLE: think → plan → select tool → execute → observe → evaluate → continue/retry/escalate → complete. "
        "Return only a short reason_summary; do not include hidden reasoning.",
        "DECISION_SCHEMA: " + json.dumps(DECISION_SCHEMA),
        "AVAILABLE_TOOLS: " + json.dumps(tools),
    ])


def build_messages(agent: AgentProfile, tools: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt(agent, tools)},
        {"role": "user", "content": f"{CONTEXT_MARKER}\n{json.dumps(context, default=str)}"},
    ]
