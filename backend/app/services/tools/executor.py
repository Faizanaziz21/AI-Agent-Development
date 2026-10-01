"""Tool Execution Service — the only path by which agents affect the outside world.

Pipeline: resolve → agent permission → argument validation → policy engine (+ agent approval
rules, tool approval flag, taint check) → approval gate → timed execution with retry/backoff →
output validation / injection screening / taint extraction → ToolCall record, metrics, events, audit.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.db import session_scope
from app.core.events import EventType, emit
from app.core.telemetry import INJECTION_FLAGS, TOOL_CALLS, TOOL_LATENCY, span
from app.models import Approval, Notification, Project, Task, ToolCall, ToolDefinition
from app.services import audit
from app.services.agents.profile import AgentProfile
from app.services.policy.engine import PolicyDecision, decide, evaluate_condition, load_policies
from app.services.security.injection import neutralize, scan
from app.services.tools import builtin  # noqa: F401  (registers built-in tools)
from app.services.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    ToolInvalidResponse,
    ToolTimeout,
    ToolTransientError,
    registry,
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s\"'<>)]+")
MAX_RESULT_CHARS = 60_000


@dataclass
class ToolOutcome:
    status: str  # SUCCESS | ERROR | DENIED | APPROVAL_REQUIRED | TIMEOUT
    tool: str
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    approval_id: str | None = None
    tool_call_id: str | None = None
    attempts: int = 0
    duration_ms: int = 0
    policy: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    transient: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS"


def _values_at(obj: Any, path: str) -> list[str]:
    parts = path.split(".")
    cur = [obj]
    for p in parts:
        nxt = []
        for c in cur:
            if isinstance(c, list):
                nxt.extend(x.get(p) for x in c if isinstance(x, dict) and p in x)
            elif isinstance(c, dict) and p in c:
                nxt.append(c[p])
        cur = nxt
    out: list[str] = []
    for c in cur:
        if isinstance(c, list):
            out.extend(str(x) for x in c)
        elif c is not None:
            out.append(str(c))
    return out


def _neutralize_tree(obj: Any, flags: list[str]) -> Any:
    if isinstance(obj, str):
        res = scan(obj)
        if res.flagged:
            flags.extend(f"injection:{i}" for i in res.indicators)
            return neutralize(obj, res)
        return obj
    if isinstance(obj, list):
        return [_neutralize_tree(x, flags) for x in obj]
    if isinstance(obj, dict):
        return {k: _neutralize_tree(v, flags) for k, v in obj.items()}
    return obj


def _truncate(result: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(result, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return result
    return {"_truncated": True, "preview": text[:MAX_RESULT_CHARS]}


class ToolExecutor:
    async def _tool_def(self, org_id: str, name: str) -> ToolDefinition | None:
        async with session_scope() as s:
            return (await s.execute(select(ToolDefinition).where(ToolDefinition.org_id == org_id, ToolDefinition.name == name))).scalar_one_or_none()

    async def _record(self, ctx: ToolContext, tool_name: str, args: dict, outcome: ToolOutcome, event_type: str, message: str) -> None:
        async with session_scope() as s:
            tc = ToolCall(org_id=ctx.org_id, project_id=ctx.project_id, task_id=ctx.task_id, execution_id=ctx.execution_id,
                          agent_key=ctx.agent_key, tool_name=tool_name, arguments=_truncate(args), result=_truncate(outcome.result) if outcome.result else None,
                          status=outcome.status, error=outcome.error, attempts=outcome.attempts, duration_ms=outcome.duration_ms,
                          policy_decision={**outcome.policy, "flags": outcome.flags})
            s.add(tc)
            await s.flush()
            outcome.tool_call_id = outcome.tool_call_id or tc.id
            if ctx.task_id:
                vals: dict[str, Any] = {"tool_calls": Task.tool_calls + 1}
                if outcome.status in ("ERROR", "TIMEOUT"):
                    vals["failures"] = Task.failures + 1
                await s.execute(update(Task).where(Task.id == ctx.task_id).values(**vals))
            emit(s, ctx.org_id, event_type, project_id=ctx.project_id, task_id=ctx.task_id, agent_key=ctx.agent_key,
                 message=message, payload={"tool": tool_name, "status": outcome.status, "tool_call_id": tc.id,
                                           "duration_ms": outcome.duration_ms, "attempts": outcome.attempts, "flags": outcome.flags})
            if outcome.status in ("DENIED",) or outcome.flags:
                await audit.record(s, ctx.org_id, actor_type="agent", actor_id=ctx.agent_key,
                                   action=f"tool.{outcome.status.lower()}", resource_type="tool", resource_id=tool_name,
                                   project_id=ctx.project_id, details={"error": outcome.error, "flags": outcome.flags, "policy": outcome.policy})

    async def execute(self, agent: AgentProfile, tool_name: str, raw_args: dict[str, Any], ctx: ToolContext,
                      approved: bool = False) -> ToolOutcome:
        with span("tool.execute", tool=tool_name, agent=agent.key, task=ctx.task_id):
            return await self._execute(agent, tool_name, raw_args or {}, ctx, approved)

    async def _execute(self, agent: AgentProfile, tool_name: str, raw_args: dict[str, Any], ctx: ToolContext,
                       approved: bool) -> ToolOutcome:
        tool: Tool | None = registry.get(tool_name)
        tdef = await self._tool_def(ctx.org_id, tool_name) if tool else None
        if tool is None or tdef is None or not tdef.enabled:
            out = ToolOutcome("DENIED", tool_name, error=f"tool '{tool_name}' is not registered or disabled", error_code="unknown_tool")
            TOOL_CALLS.labels(tool_name, "denied").inc()
            await self._record(ctx, tool_name, raw_args, out, EventType.TOOL_DENIED, out.error)
            return out
        ctx.config = dict(tdef.config or {})

        if not agent.may_use(tool_name):
            out = ToolOutcome("DENIED", tool_name, error=f"agent '{agent.key}' is not permitted to use '{tool_name}'", error_code="permission_denied")
            TOOL_CALLS.labels(tool_name, "denied").inc()
            await self._record(ctx, tool_name, raw_args, out, EventType.TOOL_DENIED, out.error)
            return out

        try:
            args = tool.input_model.model_validate(raw_args)
        except ValidationError as exc:
            errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:8])
            out = ToolOutcome("ERROR", tool_name, error=f"invalid arguments: {errs}", error_code="validation")
            TOOL_CALLS.labels(tool_name, "invalid_args").inc()
            await self._record(ctx, tool_name, raw_args, out, EventType.TOOL_FAILED, out.error)
            return out
        args_dict = args.model_dump(mode="json")

        # ---------------- policy
        flags: list[str] = []
        async with session_scope() as s:
            policies = await load_policies(s, ctx.org_id)
            template = None
            if ctx.project_id:
                p = await s.get(Project, ctx.project_id)
                template = p.template if p else None
        facts = tool.facts(args)
        tainted = [v for path in tool.sensitive_args for v in _values_at(args_dict, path) if v.lower() in ctx.tainted_values]
        pctx = {"tool": tool_name, "category": tool.category, "permission_level": tdef.permission_level,
                "agent_key": agent.key, "agent_role": agent.role, "args": args_dict, "facts": facts,
                "project_template": template, "qa_passed": bool(ctx.qa_passed_capabilities), "tainted": bool(tainted)}
        decision: PolicyDecision = decide(policies, "tool_call", pctx)
        for rule in agent.approval_rules:
            if rule.get("tool") in (tool_name, "*") and evaluate_condition(rule.get("when") or {}, pctx):
                decision.matched.append({"id": None, "name": f"agent rule ({agent.key})", "effect": "require_approval",
                                         "description": rule.get("reason", "agent approval rule")})
                if decision.effect == "allow":
                    decision.effect, decision.required_role = "require_approval", rule.get("role", "approver")
        if (tool.requires_approval or tdef.requires_approval) and decision.effect == "allow":
            decision.effect = "require_approval"
            decision.matched.append({"id": None, "name": "tool requires approval", "effect": "require_approval",
                                     "description": f"{tool_name} is configured to always require approval"})
        if tainted:
            flags.append("tainted_argument")
            if decision.effect == "allow":
                decision.effect = "require_approval"
            decision.matched.append({"id": None, "name": "untrusted-content taint", "effect": "require_approval",
                                     "description": f"argument values originate from untrusted content: {tainted[:3]}"})
        policy = decision.as_dict()

        if decision.effect == "deny" or decision.effect == "require_qa":
            code = "policy_denied" if decision.effect == "deny" else "qa_required"
            msg = ("blocked by policy: " if code == "policy_denied" else "QA approval required first: ") + "; ".join(decision.reasons)
            out = ToolOutcome("DENIED", tool_name, error=msg, error_code=code, policy=policy, flags=flags)
            TOOL_CALLS.labels(tool_name, "denied").inc()
            await self._record(ctx, tool_name, args_dict, out, EventType.TOOL_DENIED, msg)
            return out

        if decision.effect == "require_approval" and not approved:
            title, payload = tool.approval_request(args)
            risk = "high" if tainted or tdef.permission_level == "privileged" else "medium"
            out = ToolOutcome("APPROVAL_REQUIRED", tool_name, policy=policy, flags=flags)
            async with session_scope() as s:
                appr = Approval(org_id=ctx.org_id, project_id=ctx.project_id, task_id=ctx.task_id, agent_key=agent.key,
                                action_type="tool", tool_name=tool_name, title=title,
                                reason="; ".join(decision.reasons), payload=payload, risk_level=risk,
                                required_role=decision.required_role, policy_refs=decision.matched)
                s.add(appr)
                await s.flush()
                out.approval_id = appr.id
                s.add(Notification(org_id=ctx.org_id, role=decision.required_role, kind="approval",
                                   title=f"Approval needed: {title}", body=appr.reason, link=f"/approvals/{appr.id}"))
                emit(s, ctx.org_id, EventType.APPROVAL_REQUIRED, project_id=ctx.project_id, task_id=ctx.task_id, agent_key=agent.key,
                     message=f"{agent.name} requests approval: {title}", payload={"approval_id": appr.id, "tool": tool_name, "risk": risk})
                await audit.record(s, ctx.org_id, actor_type="agent", actor_id=agent.key, action="approval.requested",
                                   resource_type="approval", resource_id=appr.id, project_id=ctx.project_id,
                                   details={"tool": tool_name, "policies": [m["name"] for m in decision.matched]})
            TOOL_CALLS.labels(tool_name, "approval_required").inc()
            await self._record(ctx, tool_name, args_dict, out, EventType.TOOL_CALLED, f"{tool_name} awaiting approval")
            return out

        # ---------------- execution
        retry = {**tool.retry_policy, **(tdef.retry_policy or {})}
        max_attempts = max(1, int(retry.get("max_attempts", 3)))
        backoff = float(retry.get("backoff_seconds", 0.2))
        mult = float(retry.get("backoff_multiplier", 2.0))
        timeout = float(tdef.timeout_seconds or tool.timeout_seconds)
        chaos = ctx.chaos or {}
        async with session_scope() as s:
            emit(s, ctx.org_id, EventType.TOOL_CALLED, project_id=ctx.project_id, task_id=ctx.task_id, agent_key=agent.key,
                 message=f"{agent.name} called {tool_name}", payload={"tool": tool_name, "args_preview": json.dumps(args_dict, default=str)[:300]})
        t0 = time.perf_counter()
        last_err: ToolError | None = None
        attempt = 0
        result: Any = None
        while attempt < max_attempts:
            attempt += 1
            try:
                async def _run() -> Any:
                    if random.random() < float(chaos.get("tool_timeout_rate", 0)):
                        await asyncio.sleep(timeout + 1)
                    if random.random() < float(chaos.get("tool_failure_rate", 0)):
                        raise ToolTransientError("simulated upstream 503")
                    r = await tool.run(ctx, args)
                    if random.random() < float(chaos.get("invalid_response_rate", 0)):
                        return "<html>502 Bad Gateway</html>"
                    return r

                result = tool.validate_output(await asyncio.wait_for(_run(), timeout=timeout))
                last_err = None
                break
            except TimeoutError:
                last_err = ToolTimeout(f"{tool_name} timed out after {timeout:.1f}s")
            except ToolError as exc:
                last_err = exc
            except Exception as exc:  # noqa: BLE001 - adapter bugs surface as transient errors
                last_err = ToolTransientError(f"{type(exc).__name__}: {exc}")
            if last_err and not last_err.transient:
                break
            if attempt < max_attempts:
                await asyncio.sleep(backoff * (mult ** (attempt - 1)) * random.uniform(0.8, 1.2))
        duration = int((time.perf_counter() - t0) * 1000)
        TOOL_LATENCY.labels(tool_name).observe(duration / 1000)

        if last_err is not None:
            status = "TIMEOUT" if isinstance(last_err, ToolTimeout) else "ERROR"
            out = ToolOutcome(status, tool_name, error=str(last_err), error_code=last_err.code, attempts=attempt,
                              duration_ms=duration, policy=policy, flags=flags, transient=last_err.transient)
            TOOL_CALLS.labels(tool_name, status.lower()).inc()
            await self._record(ctx, tool_name, args_dict, out, EventType.TOOL_FAILED, f"{tool_name} failed: {last_err}")
            return out

        if tool.untrusted_output:
            blob = json.dumps(result, default=str)
            ctx.tainted_values.update(v.lower().rstrip(".,") for v in _EMAIL.findall(blob))
            ctx.tainted_values.update(v.lower().rstrip(".,") for v in _URL.findall(blob))
            result = _neutralize_tree(result, flags)
            if isinstance(result, dict) and result.get("neutralised_passages"):
                flags.append("injection:retrieval_neutralised")
            if any(f.startswith("injection:") for f in flags):
                flags = sorted(set(flags))
                INJECTION_FLAGS.labels(tool_name).inc()
                async with session_scope() as s:
                    emit(s, ctx.org_id, EventType.INJECTION_DETECTED, project_id=ctx.project_id, task_id=ctx.task_id,
                         agent_key=agent.key, message=f"Instruction-like content neutralised in {tool_name} output",
                         payload={"tool": tool_name, "indicators": flags})
        out = ToolOutcome("SUCCESS", tool_name, result=result, attempts=attempt, duration_ms=duration, policy=policy, flags=flags)
        TOOL_CALLS.labels(tool_name, "success").inc()
        await self._record(ctx, tool_name, args_dict, out, EventType.TOOL_COMPLETED,
                           f"{tool_name} completed in {duration} ms" + (f" after {attempt} attempts" if attempt > 1 else ""))
        return out


executor = ToolExecutor()

__all__ = ["ToolExecutor", "ToolOutcome", "executor", "ToolInvalidResponse"]
