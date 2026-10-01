"""Agent Runtime: executes one attempt of one task.

The runtime owns the agent loop; the Orchestrator owns task state transitions. Every step is
checkpointed (and the worker lease extended) so another worker can resume after a crash.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.events import EventType, emit
from app.models import (
    AgentDefinition,
    Evaluation,
    Execution,
    ExecutionStep,
    Objective,
    Project,
    Task,
    TaskStatus,
    ToolDefinition,
)
from app.services import memory as memory_service
from app.services.agents.context import build_messages, tool_specs
from app.services.agents.decision import AgentDecision, MalformedOutput, parse_decision
from app.services.agents.profile import AgentProfile
from app.services.budget import check_budgets
from app.services.model_gateway.gateway import AllProvidersFailed, ModelGateway, get_gateway
from app.services.model_gateway.types import CallContext, ModelRequest
from app.services.security.injection import wrap_untrusted
from app.services.tools.base import ToolContext
from app.services.tools.executor import ToolExecutor, ToolOutcome
from app.services.tools.executor import executor as default_executor

PURPOSE = {
    "evaluate_output": "evaluation", "triage_ticket": "classification", "evaluate_risk": "classification",
    "rank_candidates": "synthesis", "synthesize_deliverable": "synthesis", "research_strategy": "planning",
    "generate_report": "synthesis", "verify_firmographics": "extraction", "lookup_account": "extraction",
}
MAX_MALFORMED = 2
LOOP_REPEAT_LIMIT = 2


@dataclass
class RunResult:
    kind: str  # completed|waiting_approval|waiting_children|failed_transient|failed_permanent|escalated|budget_exceeded|lease_lost
    output: dict[str, Any] | None = None
    summary: str = ""
    error: str | None = None
    approval_id: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _signature(tool: str, args: dict) -> str:
    return hashlib.sha1(f"{tool}:{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()[:16]


def _input_hash(data: dict | None) -> str:
    """Identity of a task's work for cycle detection; runtime bookkeeping keys (`_delegated_by`, ...) are excluded."""
    public = {k: v for k, v in (data or {}).items() if not k.startswith("_")}
    return hashlib.sha1(json.dumps(public, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _summarize(result: dict | None, limit: int = 240) -> str:
    if not result:
        return ""
    parts = []
    for k, v in result.items():
        if isinstance(v, list):
            parts.append(f"{k}: {len(v)} items")
        elif isinstance(v, (str, int, float, bool)) and len(str(v)) < 80:
            parts.append(f"{k}: {v}")
        if len(", ".join(parts)) > limit:
            break
    return ", ".join(parts)[:limit]


class AgentRuntime:
    def __init__(self, gateway: ModelGateway | None = None, executor: ToolExecutor | None = None):
        self._gateway = gateway
        self.executor = executor or default_executor

    @property
    def gateway(self) -> ModelGateway:
        return self._gateway or get_gateway()

    # ------------------------------------------------------------------ persistence helpers
    async def _save(self, task_id: str, worker_id: str, checkpoint: dict, **values) -> bool:
        lease = _now() + timedelta(seconds=get_settings().worker_lease_seconds)
        async with session_scope() as s:
            res = await s.execute(update(Task).where(
                Task.id == task_id, Task.lease_owner == worker_id, Task.status == TaskStatus.RUNNING,
            ).values(checkpoint=checkpoint, lease_expires_at=lease, **values))
            return res.rowcount == 1

    async def _step(self, ex: dict, cp: dict, phase: str, *, action: str = "", tool: str | None = None, reason: str = "",
                    output: str = "", confidence: float | None = None, next_action: str = "", flags: list | None = None,
                    duration_ms: int = 0, publish: bool = True) -> None:
        cp["seq"] = cp.get("seq", 0) + 1
        async with session_scope() as s:
            s.add(ExecutionStep(org_id=ex["org_id"], project_id=ex["project_id"], task_id=ex["task_id"], execution_id=ex["id"],
                                agent_key=ex["agent_key"], seq=cp["seq"], phase=phase, action=action, tool_name=tool,
                                reason_summary=reason[:1000], output_summary=output[:1000], confidence=confidence,
                                next_action=next_action[:200], flags=flags or [], duration_ms=duration_ms))
            label = f"{phase.replace('_', ' ').title()}: " + (f"{tool} — " if tool else "") + (reason or output or action)
            await s.execute(update(Task).where(Task.id == ex["task_id"]).values(current_action=label[:300]))
            if publish:
                emit(s, ex["org_id"], EventType.AGENT_STEP, project_id=ex["project_id"], task_id=ex["task_id"],
                     agent_key=ex["agent_key"], message=label[:500],
                     payload={"phase": phase, "tool": tool, "confidence": confidence, "flags": flags or []})

    async def _finish_execution(self, ex: dict, status: str, error: str | None = None, iterations: int = 0) -> None:
        async with session_scope() as s:
            row = await s.get(Execution, ex["id"])
            if row:
                row.status, row.error, row.ended_at = status, error, _now()
                row.iterations = max(row.iterations, iterations)
                task = await s.get(Task, ex["task_id"])
                if task:
                    row.cost_usd = task.spent_usd
                    row.input_tokens = task.tokens_used

    # ------------------------------------------------------------------ load
    async def _load(self, task_id: str, worker_id: str) -> dict[str, Any] | None:
        async with session_scope() as s:
            task = await s.get(Task, task_id)
            if task is None or task.status != TaskStatus.RUNNING or task.lease_owner != worker_id:
                return None
            project = await s.get(Project, task.project_id)
            objective = (await s.execute(select(Objective).where(Objective.project_id == task.project_id)
                                         .order_by(Objective.created_at.desc()).limit(1))).scalar_one_or_none()
            agent_row = (await s.execute(select(AgentDefinition).where(
                AgentDefinition.org_id == task.org_id, AgentDefinition.key == task.agent_key))).scalar_one_or_none()
            if agent_row is None or not agent_row.enabled:
                return {"error": f"agent '{task.agent_key}' not found or disabled"}
            agent = AgentProfile.from_row(agent_row)
            enabled_tools = {t.name for t in (await s.execute(select(ToolDefinition).where(
                ToolDefinition.org_id == task.org_id, ToolDefinition.enabled.is_(True)))).scalars()}
            siblings = list((await s.execute(select(Task).where(Task.project_id == task.project_id))).scalars())
            by_id = {t.id: t for t in siblings}
            passed_tasks = {e.task_id for e in (await s.execute(select(Evaluation).where(
                Evaluation.project_id == task.project_id, Evaluation.passed.is_(True)))).scalars()}
            deps = {}
            for dep_id in task.depends_on or []:
                d = by_id.get(dep_id)
                if d:
                    deps[d.key] = {"title": d.title, "agent": d.agent_key, "capability": d.capability, "output": d.output,
                                   "output_summary": d.output_summary,
                                   "trust": "verified" if d.id in passed_tasks else "unverified (agent output)"}
            children = [{"title": c.title, "agent": c.agent_key, "capability": c.capability, "status": c.status,
                         "output": c.output, "output_summary": c.output_summary}
                        for c in siblings if c.parent_task_id == task.id]
            memories = []
            if agent.memory_config.get("session", True):
                memories = await memory_service.retrieve(s, task.org_id, f"{task.title} {task.description}",
                                                         project_id=task.project_id, k=int(agent.memory_config.get("semantic_top_k", 3)))
            cp = dict(task.checkpoint or {})
            if cp.get("attempt") != task.attempt or not cp.get("execution_id"):
                ex_row = Execution(org_id=task.org_id, project_id=task.project_id, task_id=task.id, agent_key=agent.key,
                                   attempt=task.attempt, worker_id=worker_id)
                s.add(ex_row)
                await s.flush()
                cp = {"attempt": task.attempt, "execution_id": ex_row.id, "iteration": 0, "observations": [],
                      "signatures": {}, "malformed": 0, "seq": 0, "tainted": [], "pending_action": cp.get("pending_action"),
                      "simulated_crashes": cp.get("simulated_crashes", 0)}
                emit(s, task.org_id, EventType.AGENT_STARTED, project_id=task.project_id, task_id=task.id, agent_key=agent.key,
                     message=f"{agent.name} started '{task.title}' (attempt {task.attempt})", payload={"worker": worker_id})
            else:
                ex_row = await s.get(Execution, cp["execution_id"])
                if ex_row:
                    ex_row.worker_id = worker_id
                emit(s, task.org_id, EventType.TASK_RECOVERED if cp.get("iteration") else EventType.AGENT_STARTED,
                     project_id=task.project_id, task_id=task.id, agent_key=agent.key,
                     message=f"{agent.name} resumed '{task.title}' from checkpoint (iteration {cp.get('iteration', 0)})",
                     payload={"worker": worker_id})
            ctx_base = {
                "agent": {"key": agent.key, "name": agent.name, "role": agent.role},
                "task": {"id": task.id, "project_id": task.project_id, "key": task.key, "title": task.title,
                         "description": task.description, "capability": task.capability, "input": task.input,
                         "revision": task.revision, "attempt": task.attempt},
                "objective": {"name": project.name if project else "", "text": objective.text if objective else "",
                              "parameters": objective.parameters if objective else {}},
                "dependencies": deps, "children": children, "feedback": task.feedback or [],
                "memory": memories,
                "delegation": {"allowed_agents": list(agent.can_delegate_to), "depth": task.delegation_depth,
                               "max_depth": get_settings().max_delegation_depth},
            }
            return {
                "task": {"id": task.id, "org_id": task.org_id, "project_id": task.project_id, "key": task.key, "title": task.title,
                         "agent_key": task.agent_key, "budget_usd": task.budget_usd, "delegation_depth": task.delegation_depth,
                         "plan_id": task.plan_id, "capability": task.capability, "tokens_used": task.tokens_used,
                         "parent_task_id": task.parent_task_id, "input": task.input, "children_count": len(children)},
                "ancestors": self._ancestors(task, by_id),
                "project_chaos": dict(project.chaos or {}) if project else {},
                "project_task_count": len(siblings),
                "agent": agent, "tools": tool_specs(agent, enabled_tools), "enabled": enabled_tools,
                "checkpoint": cp, "ctx_base": ctx_base, "qa_passed": bool(passed_tasks),
                "execution": {"id": cp["execution_id"], "org_id": task.org_id, "project_id": task.project_id,
                              "task_id": task.id, "agent_key": agent.key},
            }

    @staticmethod
    def _ancestors(task: Task, by_id: dict[str, Task]) -> list[dict]:
        out, cur, guard = [], task, 0
        while cur is not None and guard < 50:
            out.append({"agent_key": cur.agent_key, "capability": cur.capability, "input_hash": _input_hash(cur.input)})
            cur = by_id.get(cur.parent_task_id) if cur.parent_task_id else None
            guard += 1
        return out

    # ------------------------------------------------------------------ main loop
    async def run(self, task_id: str, worker_id: str) -> RunResult:
        st = await self._load(task_id, worker_id)
        if st is None:
            return RunResult("lease_lost", error="task not owned by this worker")
        if "error" in st:
            return RunResult("failed_permanent", error=st["error"])
        agent: AgentProfile = st["agent"]
        cp: dict = st["checkpoint"]
        ex = st["execution"]
        t = st["task"]
        tctx = ToolContext(org_id=t["org_id"], agent_key=agent.key, project_id=t["project_id"], task_id=t["id"],
                           execution_id=ex["id"], chaos=st["project_chaos"], tainted_values=set(cp.get("tainted", [])),
                           qa_passed_capabilities={"any"} if st["qa_passed"] else set())
        max_iter = agent.max_iterations

        # ---- resume a suspended action after a human decision
        pending = cp.get("pending_action")
        if pending:
            decision = pending.get("decision")
            if not decision:
                return RunResult("waiting_approval", approval_id=pending.get("approval_id"))
            if decision["status"] == "APPROVED":
                args = decision.get("payload") or pending["arguments"]
                await self._step(ex, cp, "EXECUTE_TOOL", action="resume_after_approval", tool=pending["tool"],
                                 reason=f"Human approved{' with edits' if decision.get('payload') else ''}; executing {pending['tool']}.")
                outcome = await self.executor.execute(agent, pending["tool"], args, tctx, approved=True)
                await self._observe(ex, cp, pending["tool"], args, outcome)
            else:
                cp["observations"].append({"step": cp["iteration"], "tool": pending["tool"], "arguments": pending["arguments"],
                                           "status": decision["status"], "error": decision.get("comment") or decision["status"].lower()})
                await self._step(ex, cp, "OBSERVE", action="human_decision", tool=pending["tool"],
                                 output=f"Human {decision['status'].lower().replace('_', ' ')}: {decision.get('comment', '')}")
            cp["pending_action"] = None
            cp["tainted"] = sorted(tctx.tainted_values)
            if not await self._save(t["id"], worker_id, cp):
                return RunResult("lease_lost")

        downgraded_announced = cp.get("downgraded", False)
        while cp["iteration"] < max_iter:
            # ---- budgets
            async with session_scope() as s:
                budget = await check_budgets(s, t["org_id"], t["project_id"], t["id"], agent.key)
                tokens_used = (await s.get(Task, t["id"])).tokens_used
            if budget.exceeded or tokens_used > agent.token_budget:
                scope = budget.worst_scope if budget.exceeded else "agent_tokens"
                await self._step(ex, cp, "EVALUATE", action="budget_exceeded", reason=f"{scope} budget exhausted; pausing for a human decision.",
                                 flags=["budget"])
                await self._save(t["id"], worker_id, cp)
                return RunResult("budget_exceeded", budget={**budget.as_dict(), "scope": scope}, error=f"{scope} budget exceeded")
            if budget.downgrade and not downgraded_announced:
                downgraded_announced = cp["downgraded"] = True
                max_iter = min(max_iter, cp["iteration"] + 3)
                async with session_scope() as s:
                    emit(s, t["org_id"], EventType.MODEL_DOWNGRADED, project_id=t["project_id"], task_id=t["id"], agent_key=agent.key,
                         message=f"{budget.worst_scope} budget at {budget.worst_ratio:.0%}: switching {agent.name} to a lower-cost model and capping iterations",
                         payload=budget.as_dict())

            # ---- THINK / PLAN
            context = {**st["ctx_base"], "observations": cp["observations"], "iteration": cp["iteration"],
                       "max_iterations": max_iter, "available_tools": [x["name"] for x in st["tools"]],
                       "budget": {"downgraded": budget.downgrade, "ratios": budget.as_dict()["ratios"]}}
            if cp.get("last_error"):
                context["previous_output_error"] = cp["last_error"]
            req = ModelRequest(messages=build_messages(agent, st["tools"], context), purpose=PURPOSE.get(t["capability"], "reasoning"),
                               agent_key=agent.key, tier=agent.model_policy.get("tier"), pinned_model=agent.model_policy.get("model"))
            cctx = CallContext(org_id=t["org_id"], project_id=t["project_id"], task_id=t["id"], agent_key=agent.key,
                               chaos=st["project_chaos"], downgrade=budget.downgrade)
            t0 = time.perf_counter()
            try:
                resp = await self.gateway.complete(req, cctx)
            except AllProvidersFailed as exc:
                await self._step(ex, cp, "THINK", action="model_unavailable", reason=str(exc)[:500], flags=["provider_failure"])
                await self._save(t["id"], worker_id, cp)
                await self._finish_execution(ex, "FAILED", str(exc), cp["iteration"])
                return RunResult("failed_transient", error=f"all model providers failed: {exc}")
            think_ms = int((time.perf_counter() - t0) * 1000)
            cp["iteration"] += 1
            models = cp.setdefault("models", [])
            if resp.model not in models:
                models.append(resp.model)
            if resp.fallback_from:
                async with session_scope() as s:
                    emit(s, t["org_id"], EventType.MODEL_FALLBACK, project_id=t["project_id"], task_id=t["id"], agent_key=agent.key,
                         message=f"Primary model {resp.fallback_from} failed; continued on {resp.model} ({resp.provider})",
                         payload={"from": resp.fallback_from, "to": resp.model})

            try:
                decision: AgentDecision = parse_decision(resp.content)
                cp["last_error"] = None
            except MalformedOutput as exc:
                cp["malformed"] = cp.get("malformed", 0) + 1
                cp["last_error"] = f"Your previous output was invalid ({exc}). Return exactly one JSON object matching DECISION_SCHEMA."
                await self._step(ex, cp, "EVALUATE", action="malformed_output", reason=f"Model output rejected by validator: {exc}",
                                 flags=["malformed_output"], duration_ms=think_ms, next_action="retry with repair instruction")
                if cp["malformed"] > MAX_MALFORMED:
                    await self._save(t["id"], worker_id, cp)
                    await self._finish_execution(ex, "FAILED", "repeated malformed output", cp["iteration"])
                    return RunResult("failed_transient", error="model repeatedly produced malformed output")
                if not await self._save(t["id"], worker_id, cp):
                    return RunResult("lease_lost")
                continue

            await self._step(ex, cp, "PLAN", action=decision.action, tool=decision.tool, reason=decision.reason_summary,
                             confidence=decision.confidence, next_action=decision.next_action, duration_ms=think_ms,
                             output=f"{resp.model} · {resp.input_tokens}+{resp.output_tokens} tokens · ${resp.cost_usd:.4f}")

            if decision.action == "tool_call":
                sig = _signature(decision.tool or "", decision.arguments)
                cp["signatures"][sig] = cp["signatures"].get(sig, 0) + 1
                if cp["signatures"][sig] > LOOP_REPEAT_LIMIT:
                    await self._step(ex, cp, "EVALUATE", action="loop_detected", tool=decision.tool,
                                     reason=f"Identical call to {decision.tool} repeated {cp['signatures'][sig]} times; escalating.", flags=["loop"])
                    await self._save(t["id"], worker_id, cp)
                    await self._finish_execution(ex, "ESCALATED", "agent loop detected", cp["iteration"])
                    return RunResult("escalated", error=f"agent loop detected on tool {decision.tool}")
                await self._step(ex, cp, "SELECT_TOOL", action="tool_call", tool=decision.tool, reason=decision.reason_summary,
                                 output=json.dumps(decision.arguments, default=str)[:300], publish=False)
                outcome = await self.executor.execute(agent, decision.tool or "", decision.arguments, tctx)
                cp["tainted"] = sorted(tctx.tainted_values)
                if outcome.status == "APPROVAL_REQUIRED":
                    cp["pending_action"] = {"tool": decision.tool, "arguments": decision.arguments,
                                            "approval_id": outcome.approval_id, "decision": None}
                    await self._step(ex, cp, "EXECUTE_TOOL", action="approval_required", tool=decision.tool,
                                     reason="Policy requires human approval: " + "; ".join(m["name"] for m in outcome.policy.get("matched", [])),
                                     flags=outcome.flags + ["approval"], next_action="suspend until approval decision")
                    await self._save(t["id"], worker_id, cp)
                    return RunResult("waiting_approval", approval_id=outcome.approval_id)
                await self._observe(ex, cp, decision.tool or "", decision.arguments, outcome)
                if outcome.status == "DENIED":
                    denied = cp.setdefault("denied", {})
                    denied[decision.tool] = denied.get(decision.tool, 0) + 1
                    if denied[decision.tool] >= 2:
                        await self._save(t["id"], worker_id, cp)
                        await self._finish_execution(ex, "ESCALATED", outcome.error, cp["iteration"])
                        return RunResult("escalated", error=f"repeated permission denial for {decision.tool}: {outcome.error}")
                if not await self._save(t["id"], worker_id, cp):
                    return RunResult("lease_lost")
                continue

            if decision.action == "delegate":
                res = await self._delegate(st, cp, ex, decision)
                if res is not None:
                    await self._save(t["id"], worker_id, cp)
                    return res
                if not await self._save(t["id"], worker_id, cp):
                    return RunResult("lease_lost")
                continue

            if decision.action == "escalate":
                await self._step(ex, cp, "EVALUATE", action="escalate", reason=decision.reason_summary, flags=["escalation"])
                await self._save(t["id"], worker_id, cp)
                await self._finish_execution(ex, "ESCALATED", decision.reason_summary, cp["iteration"])
                return RunResult("escalated", error=decision.reason_summary)

            # ---- final
            output = decision.output or {}
            await self._step(ex, cp, "EVALUATE", action="self_check", reason="Output validated against decision schema.",
                             output=decision.output_summary or _summarize(output), confidence=decision.confidence, publish=False)
            await self._step(ex, cp, "COMPLETE", action="final", reason=decision.reason_summary,
                             output=decision.output_summary or _summarize(output), confidence=decision.confidence)
            await self._save(t["id"], worker_id, cp)
            await self._finish_execution(ex, "COMPLETED", None, cp["iteration"])
            return RunResult("completed", output=output, summary=decision.output_summary or _summarize(output),
                             confidence=decision.confidence)

        await self._step(ex, cp, "EVALUATE", action="iteration_limit", reason=f"Reached max iterations ({max_iter}).", flags=["iteration_limit"])
        await self._save(t["id"], worker_id, cp)
        await self._finish_execution(ex, "FAILED", "iteration limit", cp["iteration"])
        return RunResult("failed_permanent", error=f"iteration limit ({max_iter}) reached without completion")

    async def _observe(self, ex: dict, cp: dict, tool: str, args: dict, outcome: ToolOutcome) -> None:
        obs: dict[str, Any] = {"step": cp["iteration"], "tool": tool, "arguments": args, "status": outcome.status}
        if outcome.ok:
            obs["result"] = wrap_untrusted(outcome.result or {}, source=f"tool:{tool}")
        else:
            obs["error"] = outcome.error
            obs["error_code"] = outcome.error_code
        cp["observations"].append(obs)
        summary = (_summarize(outcome.result) if outcome.ok else f"{outcome.status}: {outcome.error}")
        if outcome.attempts > 1:
            summary += f" (after {outcome.attempts} attempts)"
        await self._step(ex, cp, "OBSERVE", action=outcome.status.lower(), tool=tool, output=summary, flags=outcome.flags,
                         duration_ms=outcome.duration_ms)

    async def _delegate(self, st: dict, cp: dict, ex: dict, decision: AgentDecision) -> RunResult | None:
        s_ = get_settings()
        t, agent = st["task"], st["agent"]
        problems = []
        if t["delegation_depth"] + 1 > s_.max_delegation_depth:
            problems.append(f"max delegation depth {s_.max_delegation_depth} reached")
        if t["children_count"] + len(decision.subtasks) > s_.max_children_per_task:
            problems.append(f"fan-out limit {s_.max_children_per_task} exceeded")
        if st["project_task_count"] + len(decision.subtasks) > s_.max_tasks_per_project:
            problems.append("project task cap reached")
        async with session_scope() as s:
            known = {a.key for a in (await s.execute(select(AgentDefinition).where(
                AgentDefinition.org_id == t["org_id"], AgentDefinition.enabled.is_(True)))).scalars()}
        for sub in decision.subtasks:
            if sub.agent_key not in known:
                problems.append(f"unknown agent '{sub.agent_key}'")
            elif "*" not in agent.can_delegate_to and sub.agent_key not in agent.can_delegate_to:
                problems.append(f"{agent.key} may not delegate to {sub.agent_key}")
            ih = _input_hash(sub.input)
            if any(a["agent_key"] == sub.agent_key and a["capability"] == sub.capability and a["input_hash"] == ih for a in st["ancestors"]):
                problems.append(f"delegation cycle: '{sub.capability}' already in ancestor chain")
        if problems:
            cp["observations"].append({"step": cp["iteration"], "tool": "delegate", "status": "DENIED",
                                       "error": "delegation rejected: " + "; ".join(sorted(set(problems)))})
            await self._step(ex, cp, "EVALUATE", action="delegation_rejected", reason="; ".join(sorted(set(problems))), flags=["delegation_guard"])
            sig = "delegate:" + _signature("delegate", {"n": [x.title for x in decision.subtasks]})
            cp["signatures"][sig] = cp["signatures"].get(sig, 0) + 1
            if cp["signatures"][sig] > 1:
                await self._finish_execution(ex, "ESCALATED", "repeated invalid delegation", cp["iteration"])
                return RunResult("escalated", error="repeated invalid delegation: " + "; ".join(problems))
            return None
        async with session_scope() as s:
            per_child_budget = round(max(0.25, t["budget_usd"] / max(len(decision.subtasks), 1)), 2)
            for i, sub in enumerate(decision.subtasks, 1):
                child = Task(org_id=t["org_id"], project_id=t["project_id"], plan_id=t["plan_id"], parent_task_id=t["id"],
                             key=f"{t['key']}.sub{t['children_count'] + i}", title=sub.title, description=sub.description,
                             capability=sub.capability, agent_key=sub.agent_key, input={**sub.input, "_delegated_by": agent.key},
                             delegation_depth=t["delegation_depth"] + 1, budget_usd=per_child_budget, status=TaskStatus.PENDING, attempt=1,
                             agent_instance=f"{sub.agent_key.replace('_', ' ').title()} (delegated)")
                s.add(child)
                await s.flush()
                emit(s, t["org_id"], EventType.TASK_DELEGATED, project_id=t["project_id"], task_id=child.id, agent_key=agent.key,
                     message=f"{agent.name} delegated '{sub.title}' to {sub.agent_key}",
                     payload={"parent_task_id": t["id"], "child_task_id": child.id, "assigned_agent": sub.agent_key})
        await self._step(ex, cp, "EXECUTE_TOOL", action="delegate", reason=decision.reason_summary,
                         output=f"created {len(decision.subtasks)} subtasks", next_action="wait for subtasks")
        cp["delegated_rounds"] = cp.get("delegated_rounds", 0) + 1
        return RunResult("waiting_children")
