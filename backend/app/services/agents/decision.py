"""Model output contract + parser with repair. Model output is untrusted until validated here."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class MalformedOutput(Exception):
    pass


class SubtaskSpec(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    agent_key: str = Field(min_length=2, max_length=80)
    capability: str = Field(default="generic", max_length=80)
    description: str = Field(default="", max_length=2000)
    input: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    action: Literal["tool_call", "final", "delegate", "escalate"]
    reason_summary: str = Field(default="", max_length=1000)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    output_summary: str = Field(default="", max_length=2000)
    subtasks: list[SubtaskSpec] = Field(default_factory=list, max_length=20)
    next_action: str = Field(default="", max_length=300)

    @field_validator("reason_summary", "output_summary", "next_action")
    @classmethod
    def _single_paragraph(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()

    def check(self) -> None:
        if self.action == "tool_call" and not self.tool:
            raise MalformedOutput("tool_call requires 'tool'")
        if self.action == "final" and self.output is None:
            raise MalformedOutput("final requires 'output'")
        if self.action == "delegate" and not self.subtasks:
            raise MalformedOutput("delegate requires 'subtasks'")


DECISION_SCHEMA = {
    "action": "tool_call | final | delegate | escalate",
    "reason_summary": "one or two sentences explaining the choice (no hidden reasoning)",
    "confidence": "0..1",
    "tool": "tool name when action=tool_call",
    "arguments": "tool arguments matching its input_schema",
    "output": "result object when action=final",
    "output_summary": "short human-readable summary of output",
    "subtasks": "[{title, agent_key, capability, description, input}] when action=delegate",
    "next_action": "what you expect to do next",
}


def _extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise MalformedOutput("no JSON object found in model output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    raise MalformedOutput("unterminated JSON object in model output")


def parse_json_object(text: str) -> dict[str, Any]:
    raw = _extract_json(text)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", raw)  # trailing commas
        try:
            obj = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise MalformedOutput(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise MalformedOutput("top-level JSON must be an object")
    return obj


def parse_decision(text: str) -> AgentDecision:
    obj = parse_json_object(text)
    try:
        d = AgentDecision.model_validate(obj)
    except ValidationError as exc:
        raise MalformedOutput("decision schema violation: " + "; ".join(e["msg"] for e in exc.errors()[:5])) from exc
    d.check()
    return d
