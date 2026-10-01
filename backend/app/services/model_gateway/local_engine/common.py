"""Helpers shared by LocalReasoningEngine skill policies.

A skill policy is a pure function `(ctx) -> decision dict`. `ctx` is exactly the TASK_CONTEXT
JSON the runtime sends to any model; policies must not reach outside it.
"""

from __future__ import annotations

from typing import Any


class Ctx:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.task: dict = raw.get("task", {})
        self.input: dict = self.task.get("input", {}) or {}
        self.objective: dict = raw.get("objective", {}) or {}
        self.params: dict = self.objective.get("parameters", {}) or {}
        self.deps: dict = raw.get("dependencies", {}) or {}
        self.children: list = raw.get("children", []) or []
        self.observations: list = raw.get("observations", []) or []
        self.feedback: list = raw.get("feedback", []) or []
        self.tools: list = raw.get("available_tools", []) or []
        self.iteration: int = raw.get("iteration", 0)
        self.max_iterations: int = raw.get("max_iterations", 8)
        self.agent: dict = raw.get("agent", {})

    def results(self, tool: str, ok_only: bool = True) -> list[dict]:
        out = []
        for o in self.observations:
            if o.get("tool") != tool:
                continue
            if ok_only and o.get("status") != "SUCCESS":
                continue
            r = o.get("result")
            if isinstance(r, dict) and r.get("untrusted_data"):
                r = r.get("content")
            out.append(r or {})
        return out

    def last(self, tool: str) -> dict | None:
        r = self.results(tool)
        return r[-1] if r else None

    def attempted(self, tool: str) -> list[dict]:
        return [o for o in self.observations if o.get("tool") == tool]

    def human_decision(self, tool: str) -> dict | None:
        for o in reversed(self.observations):
            if o.get("tool") == tool and o.get("status") in ("REJECTED", "CHANGES_REQUESTED"):
                return o
        return None

    def dep_output(self, *keys_or_caps: str) -> dict:
        """Find a dependency output by task key prefix or capability."""
        for k, d in self.deps.items():
            for want in keys_or_caps:
                if k == want or k.startswith(want) or d.get("capability") == want:
                    return d.get("output") or {}
        return {}

    def dep_outputs(self, capability: str) -> list[dict]:
        return [d.get("output") or {} for d in self.deps.values() if d.get("capability") == capability]

    def feedback_text(self) -> str:
        return " ".join(f.get("text", "") for f in self.feedback)

    def feedback_issues(self) -> list[dict]:
        out = []
        for f in self.feedback:
            out.extend(f.get("issues", []) or [])
        return out


def call(tool: str, arguments: dict, reason: str, next_action: str = "observe result", confidence: float = 0.8) -> dict:
    return {"action": "tool_call", "tool": tool, "arguments": arguments, "reason_summary": reason,
            "confidence": confidence, "next_action": next_action}


def final(output: dict, summary: str, reason: str = "", confidence: float = 0.85) -> dict:
    return {"action": "final", "output": output, "output_summary": summary,
            "reason_summary": reason or "All required information gathered; returning result.",
            "confidence": confidence, "next_action": "hand off to orchestrator"}


def delegate(subtasks: list[dict], reason: str) -> dict:
    return {"action": "delegate", "subtasks": subtasks, "reason_summary": reason, "confidence": 0.75,
            "next_action": "wait for subtasks, then merge results"}


def escalate(reason: str) -> dict:
    return {"action": "escalate", "reason_summary": reason, "confidence": 0.3, "next_action": "await human guidance"}


def chunks(seq: list, n: int) -> list[list]:
    return [seq[i: i + n] for i in range(0, len(seq), n)]
