"""Agent Orchestrator: owns task/project state transitions and scheduling of the task DAG."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.events import EventType, emit
from app.core.telemetry import TASKS_TOTAL
from app.models import (
    AgentDefinition,
    Approval,
    ApprovalStatus,
    Evaluation,
    MemoryItem,
    Notification,
    Organization,
    Project,
    ProjectStatus,
    Task,
    TaskRevision,
    TaskStatus,
    TrustLevel,
)
from app.services import audit
from app.services.agents.runtime import RunResult
from app.services.queue import get_queue, make_job

log = logging.getLogger("agentos.orchestrator")


def _now() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1)))


async def enqueue_task(task_id: str, org_id: str, priority: int = 5, delay_s: float = 0) -> None:
    await get_queue().put(make_job("run_task", task_id, org_id, priority), delay_s)


async def enqueue_plan(project_id: str, org_id: str) -> None:
    await get_queue().put(make_job("plan_project", project_id, org_id, 9))


# ---------------------------------------------------------------------------- project lifecycle
async def start_project(project_id: str, actor_id: str = "system") -> None:
    async with session_scope() as s:
        p = await s.get(Project, project_id)
        if p.status not in (ProjectStatus.DRAFT, ProjectStatus.FAILED):
            raise ValueError(f"project is {p.status}")
        p.status, p.started_at = ProjectStatus.PLANNING, _now()
        emit(s, p.org_id, EventType.PROJECT_STARTED, project_id=p.id, message=f"Project '{p.name}' started")
        await audit.record(s, p.org_id, actor_type="user", actor_id=actor_id, action="project.started", resource_type="project",
                           resource_id=p.id, project_id=p.id)
        org_id = p.org_id
    await enqueue_plan(project_id, org_id)


async def cancel_project(project_id: str, actor_id: str) -> None:
    async with session_scope() as s:
        p = await s.get(Project, project_id)
        tasks = (await s.execute(select(Task).where(Task.project_id == project_id))).scalars()
        for t in tasks:
            if t.status not in TaskStatus.TERMINAL:
                t.status, t.lease_owner = TaskStatus.CANCELLED, None
        await s.execute(update(Approval).where(Approval.project_id == project_id, Approval.status == ApprovalStatus.PENDING)
                        .values(status=ApprovalStatus.EXPIRED))
        p.status, p.completed_at = ProjectStatus.CANCELLED, _now()
        emit(s, p.org_id, EventType.PROJECT_CANCELLED, project_id=p.id, message="Project cancelled")
        await audit.record(s, p.org_id, actor_type="user", actor_id=actor_id, action="project.cancelled", resource_type="project",
                           resource_id=p.id, project_id=p.id)


async def schedule(project_id: str) -> None:
    """Advance the DAG: queue ready tasks, block tasks with failed deps, resume parents, close the project."""
    to_enqueue: list[tuple[str, str, int]] = []
    async with session_scope() as s:
        p = await s.get(Project, project_id)
        if p is None or p.status in (ProjectStatus.CANCELLED, ProjectStatus.COMPLETED, ProjectStatus.DRAFT, ProjectStatus.PLANNING):
            return
        tasks = list((await s.execute(select(Task).where(Task.project_id == project_id))).scalars())
        by_id = {t.id: t for t in tasks}
        for t in tasks:
            if t.status == TaskStatus.PENDING:
                deps = [by_id[d] for d in t.depends_on or [] if d in by_id]
                if any(d.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED) for d in deps):
                    t.status = TaskStatus.BLOCKED
                    emit(s, t.org_id, EventType.TASK_BLOCKED, project_id=project_id, task_id=t.id, agent_key=t.agent_key,
                         message=f"'{t.title}' blocked: a dependency did not complete")
                elif all(d.status == TaskStatus.COMPLETED for d in deps):
                    t.status = TaskStatus.QUEUED
                    to_enqueue.append((t.id, t.org_id, t.priority))
                    emit(s, t.org_id, EventType.TASK_QUEUED, project_id=project_id, task_id=t.id, agent_key=t.agent_key,
                         message=f"'{t.title}' queued for {t.agent_instance or t.agent_key}")
            elif t.status == TaskStatus.WAITING_CHILDREN:
                kids = [c for c in tasks if c.parent_task_id == t.id]
                if kids and all(c.status in TaskStatus.TERMINAL or c.status == TaskStatus.BLOCKED for c in kids):
                    t.status = TaskStatus.QUEUED
                    to_enqueue.append((t.id, t.org_id, t.priority))
                    emit(s, t.org_id, EventType.TASK_QUEUED, project_id=project_id, task_id=t.id, agent_key=t.agent_key,
                         message=f"'{t.title}' resuming with results from {len(kids)} subtasks")
        top = [t for t in tasks if t.parent_task_id is None and not (t.input or {}).get("_review_of")]
        if top and all(t.status in TaskStatus.TERMINAL or t.status == TaskStatus.BLOCKED for t in top):
            if all(t.status == TaskStatus.COMPLETED for t in top):
                deliverable_task = next((t for t in top if (t.input or {}).get("deliverable")), top[-1])
                p.deliverable = {"task_key": deliverable_task.key, "summary": deliverable_task.output_summary,
                                 **(deliverable_task.output or {})}
                p.status, p.completed_at = ProjectStatus.COMPLETED, _now()
                emit(s, p.org_id, EventType.PROJECT_COMPLETED, project_id=p.id, agent_key="supervisor",
                     message=f"Project completed: {deliverable_task.output_summary}", payload={"spent_usd": round(p.spent_usd, 4)})
                s.add(Notification(org_id=p.org_id, role="operator", kind="project", title=f"Project '{p.name}' completed",
                                   body=deliverable_task.output_summary, link=f"/projects/{p.id}"))
            else:
                p.status, p.completed_at = ProjectStatus.FAILED, _now()
                failed = [t.title for t in top if t.status != TaskStatus.COMPLETED]
                emit(s, p.org_id, EventType.PROJECT_FAILED, project_id=p.id, message=f"Project failed: {', '.join(failed[:5])}")
            await audit.record(s, p.org_id, actor_type="system", actor_id="orchestrator", action=f"project.{p.status.lower()}",
                               resource_type="project", resource_id=p.id, project_id=p.id, details={"spent_usd": p.spent_usd})
        else:
            busy = any(t.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRY_SCHEDULED, TaskStatus.IN_REVIEW) for t in tasks)
            waiting = any(t.status in (TaskStatus.WAITING_APPROVAL, TaskStatus.ESCALATED) for t in tasks)
            p.status = ProjectStatus.WAITING_APPROVAL if waiting and not busy and not to_enqueue else ProjectStatus.RUNNING
    for tid, org, prio in to_enqueue:
        await enqueue_task(tid, org, prio)


# ---------------------------------------------------------------------------- worker interaction
async def claim(task_id: str, worker_id: str) -> bool:
    lease = _now() + timedelta(seconds=get_settings().worker_lease_seconds)
    async with session_scope() as s:
        res = await s.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED).values(
            status=TaskStatus.RUNNING, lease_owner=worker_id, lease_expires_at=lease, started_at=_now()))
        if res.rowcount != 1:
            return False
        t = await s.get(Task, task_id)
        TASKS_TOTAL.labels("RUNNING", t.agent_key).inc()
        return True


def _set(t: Task, status: str) -> None:
    t.status = status
    TASKS_TOTAL.labels(status, t.agent_key).inc()
    if status in TaskStatus.TERMINAL:
        t.completed_at = _now()
    if status != TaskStatus.RUNNING:
        t.lease_owner, t.lease_expires_at = None, None


async def _escalation_approval(s, t: Task, title: str, reason: str, action_type: str, payload: dict, risk: str = "high") -> Approval:
    a = Approval(org_id=t.org_id, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key, action_type=action_type,
                 title=title, reason=reason, payload=payload, risk_level=risk, required_role="operator")
    s.add(a)
    await s.flush()
    s.add(Notification(org_id=t.org_id, role="operator", kind="escalation", title=title, body=reason, link=f"/approvals/{a.id}"))
    emit(s, t.org_id, EventType.APPROVAL_REQUIRED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
         message=f"Human decision needed: {title}", payload={"approval_id": a.id, "action_type": action_type})
    return a


async def _escalate_or_fail(s, t: Task, error: str) -> None:
    agent = (await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == t.org_id, AgentDefinition.key == t.agent_key))).scalar_one_or_none()
    rules = (agent.escalation_rules or {}) if agent else {}
    alternate = rules.get("alternate_agent")
    tried = (t.checkpoint or {}).get("reassigned_from", [])
    if alternate and alternate not in tried and alternate != t.agent_key:
        prev = t.agent_key
        t.agent_key, t.agent_instance = alternate, f"{alternate.replace('_', ' ').title()} (reassigned)"
        t.attempt += 1
        t.max_attempts = t.attempt + 1
        t.checkpoint = {"reassigned_from": [*tried, prev]}
        _set(t, TaskStatus.QUEUED)
        emit(s, t.org_id, EventType.TASK_REASSIGNED, project_id=t.project_id, task_id=t.id, agent_key=alternate,
             message=f"'{t.title}' reassigned from {prev} to {alternate} after: {error[:200]}")
        return
    _set(t, TaskStatus.ESCALATED)
    t.error = error
    emit(s, t.org_id, EventType.TASK_ESCALATED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
         message=f"'{t.title}' escalated to a human: {error[:300]}")
    await _escalation_approval(s, t, f"Task needs attention: {t.title}", error, "escalation",
                               {"error": error, "options": ["approve = retry", "edit {\"agent_key\": ...} = reassign", "reject = fail task"],
                                "attempts": t.attempt, "agent": t.agent_key})


async def handle_result(task_id: str, result: RunResult) -> None:
    requeue: list[tuple[str, str, int, float]] = []
    async with session_scope() as s:
        t = await s.get(Task, task_id)
        if t is None or t.status != TaskStatus.RUNNING:
            return
        project_id = t.project_id
        k = result.kind
        if k == "lease_lost":
            return
        if k == "completed":
            t.output, t.output_summary, t.error = result.output, result.summary, None
            review_of = (t.input or {}).get("_review_of")
            if review_of:
                _set(t, TaskStatus.COMPLETED)
                emit(s, t.org_id, EventType.TASK_COMPLETED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                     message=f"QA review done: {result.summary}")
                await _process_review(s, t, review_of, requeue)
            elif t.requires_review:
                _set(t, TaskStatus.IN_REVIEW)
                review = Task(org_id=t.org_id, project_id=t.project_id, plan_id=t.plan_id, key=f"{t.key}.review{t.revision + 1}",
                              title=f"QA review: {t.title}" + (f" (revision {t.revision})" if t.revision else ""),
                              capability="evaluate_output", agent_key="qa", agent_instance="QA / Critic Agent",
                              depends_on=[t.id], input={"_review_of": t.id, "criteria": t.review_criteria, "revision": t.revision},
                              status=TaskStatus.QUEUED, attempt=1, priority=min(10, t.priority + 1), budget_usd=0.5)
                s.add(review)
                await s.flush()
                requeue.append((review.id, review.org_id, review.priority, 0))
                emit(s, t.org_id, EventType.TASK_REVIEW_STARTED, project_id=t.project_id, task_id=review.id, agent_key="qa",
                     message=f"QA Agent reviewing '{t.title}'", payload={"target_task_id": t.id})
            else:
                _set(t, TaskStatus.COMPLETED)
                emit(s, t.org_id, EventType.TASK_COMPLETED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                     message=f"{t.agent_instance or t.agent_key} ✓ {result.summary}", payload={"confidence": result.confidence})
        elif k == "waiting_approval":
            _set(t, TaskStatus.WAITING_APPROVAL)
            t.current_action = "Waiting for human approval"
        elif k == "waiting_children":
            _set(t, TaskStatus.WAITING_CHILDREN)
            t.current_action = "Waiting for delegated subtasks"
        elif k == "failed_transient":
            t.error = result.error
            emit(s, t.org_id, EventType.TASK_FAILED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                 message=f"'{t.title}' attempt {t.attempt} failed: {(result.error or '')[:300]}", payload={"transient": True})
            if t.attempt < t.max_attempts:
                delay = backoff_seconds(t.attempt)
                t.attempt += 1
                t.retries += 1
                t.next_run_at = _now() + timedelta(seconds=delay)
                _set(t, TaskStatus.RETRY_SCHEDULED)
                emit(s, t.org_id, EventType.TASK_RETRY_SCHEDULED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                     message=f"Retry {t.attempt}/{t.max_attempts} of '{t.title}' in {delay:.0f}s (checkpoint preserved)")
            else:
                await _escalate_or_fail(s, t, result.error or "failed")
        elif k in ("failed_permanent", "escalated"):
            emit(s, t.org_id, EventType.TASK_FAILED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                 message=f"'{t.title}' failed: {(result.error or '')[:300]}", payload={"transient": False})
            await _escalate_or_fail(s, t, result.error or k)
        elif k == "budget_exceeded":
            _set(t, TaskStatus.WAITING_APPROVAL)
            b = result.budget
            scope = b.get("scope", "task")
            limit = (b.get("limits") or {}).get(scope, 0)
            emit(s, t.org_id, EventType.BUDGET_EXCEEDED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                 message=f"{scope.title()} budget reached for '{t.title}' — asking whether to continue", payload=b)
            await _escalation_approval(s, t, f"Budget limit reached ({scope}) — continue?", result.error or "budget exceeded",
                                       "budget_extension", {"scope": scope, "limit_usd": limit, "spent": b.get("spent"),
                                                            "increase_usd": round(max(1.0, limit * 0.5), 2)}, risk="medium")
    for tid, org, prio, delay in requeue:
        await enqueue_task(tid, org, prio, delay)
    await schedule(project_id)


async def _process_review(s, review: Task, target_id: str, requeue: list) -> None:
    target = await s.get(Task, target_id)
    out = review.output or {}
    threshold = float((target.review_criteria or {}).get("threshold", get_settings().default_quality_threshold))
    overall = float(out.get("overall", 0))
    passed = overall >= threshold
    s.add(Evaluation(org_id=target.org_id, project_id=target.project_id, task_id=target.id, evaluator_agent=review.agent_key,
                     revision=target.revision, scores=out.get("scores", {}), overall=overall, threshold=threshold, passed=passed,
                     feedback=out.get("feedback", ""), issues=out.get("issues", [])))
    s.add(TaskRevision(org_id=target.org_id, task_id=target.id, revision=target.revision, output=target.output,
                       output_summary=target.output_summary, score=overall, feedback=out.get("feedback", "")))
    target.quality_score = overall
    if passed:
        _set(target, TaskStatus.COMPLETED)
        await s.execute(update(MemoryItem).where(MemoryItem.task_id == target.id, MemoryItem.trust_level == TrustLevel.UNVERIFIED)
                        .values(trust_level=TrustLevel.VERIFIED, verified_by="qa"))
        emit(s, target.org_id, EventType.TASK_COMPLETED, project_id=target.project_id, task_id=target.id, agent_key=target.agent_key,
             message=f"{target.agent_instance or target.agent_key} ✓ {target.output_summary} (QA {overall:.2f} ≥ {threshold:.2f})",
             payload={"quality_score": overall})
        return
    max_rev = int((target.review_criteria or {}).get("max_revisions", get_settings().max_revisions))
    target.feedback = [*(target.feedback or []), {"source": "qa", "revision": target.revision, "text": out.get("feedback", ""),
                                                   "issues": out.get("issues", [])[:30], "score": overall}]
    if target.revision < max_rev:
        target.revision += 1
        target.checkpoint = None
        _set(target, TaskStatus.QUEUED)
        requeue.append((target.id, target.org_id, target.priority, 0))
        emit(s, target.org_id, EventType.TASK_REVISION_REQUESTED, project_id=target.project_id, task_id=target.id, agent_key="qa",
             message=f"QA rejected '{target.title}' ({overall:.2f} < {threshold:.2f}): {out.get('feedback', '')[:200]} — returned for revision {target.revision}",
             payload={"score": overall, "issues": len(out.get("issues", []))})
    else:
        _set(target, TaskStatus.ESCALATED)
        emit(s, target.org_id, EventType.TASK_ESCALATED, project_id=target.project_id, task_id=target.id, agent_key="qa",
             message=f"'{target.title}' failed QA after {target.revision} revisions — escalated")
        await _escalation_approval(s, target, f"Quality below threshold: {target.title}",
                                   f"Score {overall:.2f} < {threshold:.2f} after {target.revision} revisions. {out.get('feedback', '')}",
                                   "quality_escalation", {"score": overall, "issues": out.get("issues", [])[:20],
                                                          "options": ["approve = accept as is", "request changes = revise", "reject = fail"]})


# ---------------------------------------------------------------------------- human decisions
async def apply_approval(approval_id: str) -> None:
    project_id = None
    requeue: list[tuple[str, str, int]] = []
    async with session_scope() as s:
        a = await s.get(Approval, approval_id)
        if a is None or a.task_id is None:
            return
        t = await s.get(Task, a.task_id)
        if t is None or t.status in TaskStatus.TERMINAL:
            return
        project_id = t.project_id
        status = a.status
        if a.action_type == "tool":
            cp = dict(t.checkpoint or {})
            pending = dict(cp.get("pending_action") or {})
            if pending.get("approval_id") != a.id:
                return
            pending["decision"] = {"status": status, "payload": a.edited_payload, "comment": a.comment}
            cp["pending_action"] = pending
            t.checkpoint = cp
            _set(t, TaskStatus.QUEUED)
            requeue.append((t.id, t.org_id, t.priority))
        elif a.action_type == "budget_extension":
            if status == ApprovalStatus.APPROVED:
                inc = float((a.edited_payload or a.payload).get("increase_usd", 1.0))
                scope = a.payload.get("scope", "task")
                if scope == "project":
                    p = await s.get(Project, t.project_id)
                    p.budget_usd += inc
                elif scope == "organization":
                    org = await s.get(Organization, t.org_id)
                    org.monthly_budget_usd += inc
                elif scope == "agent":
                    ag = (await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == t.org_id, AgentDefinition.key == t.agent_key))).scalar_one()
                    ag.cost_budget_usd += inc
                else:
                    t.budget_usd += inc
                _set(t, TaskStatus.QUEUED)
                requeue.append((t.id, t.org_id, t.priority))
            else:
                t.error = "stopped by human: budget not extended"
                _set(t, TaskStatus.FAILED)
        elif a.action_type == "escalation":
            if status in (ApprovalStatus.APPROVED, ApprovalStatus.CHANGES_REQUESTED):
                new_agent = (a.edited_payload or {}).get("agent_key")
                if new_agent:
                    t.agent_key, t.agent_instance = new_agent, f"{new_agent.replace('_', ' ').title()} (reassigned)"
                if a.comment:
                    t.feedback = [*(t.feedback or []), {"source": "human", "text": a.comment, "issues": []}]
                t.attempt += 1
                t.max_attempts = max(t.max_attempts, t.attempt + 1)
                t.checkpoint = None
                t.error = None
                _set(t, TaskStatus.QUEUED)
                requeue.append((t.id, t.org_id, t.priority))
            else:
                _set(t, TaskStatus.FAILED)
        elif a.action_type == "quality_escalation":
            if status == ApprovalStatus.APPROVED:
                _set(t, TaskStatus.COMPLETED)
                emit(s, t.org_id, EventType.TASK_COMPLETED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                     message=f"'{t.title}' accepted by human reviewer despite QA score")
            elif status == ApprovalStatus.CHANGES_REQUESTED:
                t.feedback = [*(t.feedback or []), {"source": "human", "text": a.comment, "issues": []}]
                t.revision += 1
                t.checkpoint = None
                _set(t, TaskStatus.QUEUED)
                requeue.append((t.id, t.org_id, t.priority))
            else:
                _set(t, TaskStatus.FAILED)
    for tid, org, prio in requeue:
        await enqueue_task(tid, org, prio)
    if project_id:
        await schedule(project_id)


# ---------------------------------------------------------------------------- operator actions
async def retry_task(task_id: str, actor_id: str) -> Task:
    async with session_scope() as s:
        t = await s.get(Task, task_id)
        if t.status not in (TaskStatus.FAILED, TaskStatus.ESCALATED, TaskStatus.BLOCKED, TaskStatus.CANCELLED):
            raise ValueError(f"cannot retry a task in status {t.status}")
        t.attempt += 1
        t.max_attempts = max(t.max_attempts, t.attempt + 1)
        t.error, t.checkpoint = None, None
        _set(t, TaskStatus.PENDING)
        # unblock dependants so the DAG can continue
        for d in (await s.execute(select(Task).where(Task.project_id == t.project_id, Task.status == TaskStatus.BLOCKED))).scalars():
            d.status = TaskStatus.PENDING
        p = await s.get(Project, t.project_id)
        if p.status in (ProjectStatus.FAILED, ProjectStatus.WAITING_APPROVAL):
            p.status, p.completed_at = ProjectStatus.RUNNING, None
        await s.execute(update(Approval).where(Approval.task_id == t.id, Approval.status == ApprovalStatus.PENDING).values(status=ApprovalStatus.EXPIRED))
        await audit.record(s, t.org_id, actor_type="user", actor_id=actor_id, action="task.retried", resource_type="task",
                           resource_id=t.id, project_id=t.project_id)
        project_id = t.project_id
    await schedule(project_id)
    return t


async def cancel_task(task_id: str, actor_id: str) -> None:
    async with session_scope() as s:
        t = await s.get(Task, task_id)
        if t.status in TaskStatus.TERMINAL:
            raise ValueError("task already finished")
        _set(t, TaskStatus.CANCELLED)
        emit(s, t.org_id, EventType.TASK_CANCELLED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
             message=f"'{t.title}' cancelled by operator")
        await audit.record(s, t.org_id, actor_type="user", actor_id=actor_id, action="task.cancelled", resource_type="task",
                           resource_id=t.id, project_id=t.project_id)
        project_id = t.project_id
    await schedule(project_id)


async def reassign_task(task_id: str, agent_key: str, actor_id: str) -> None:
    async with session_scope() as s:
        t = await s.get(Task, task_id)
        agent = (await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == t.org_id, AgentDefinition.key == agent_key))).scalar_one_or_none()
        if agent is None or not agent.enabled:
            raise ValueError("unknown agent")
        if t.status in (TaskStatus.RUNNING, TaskStatus.COMPLETED):
            raise ValueError(f"cannot reassign a task in status {t.status}")
        prev = t.agent_key
        t.agent_key, t.agent_instance, t.checkpoint = agent_key, agent.name, None
        emit(s, t.org_id, EventType.TASK_REASSIGNED, project_id=t.project_id, task_id=t.id, agent_key=agent_key,
             message=f"'{t.title}' reassigned from {prev} to {agent_key} by operator")
        await audit.record(s, t.org_id, actor_type="user", actor_id=actor_id, action="task.reassigned", resource_type="task",
                           resource_id=t.id, project_id=t.project_id, details={"from": prev, "to": agent_key})
        requeue = t.status in (TaskStatus.QUEUED, TaskStatus.ESCALATED, TaskStatus.FAILED)
        if requeue:
            t.attempt += 1
            t.max_attempts = max(t.max_attempts, t.attempt + 1)
            _set(t, TaskStatus.QUEUED)
        tid, org, prio, project_id = t.id, t.org_id, t.priority, t.project_id
    if requeue:
        await enqueue_task(tid, org, prio)
    await schedule(project_id)


async def recover(stale_queued_after_s: float = 30.0) -> dict[str, Any]:
    """Idempotent recovery sweep run by every worker process.

    * RUNNING tasks whose lease expired (worker crashed / restarted) → QUEUED, resume from checkpoint.
    * RETRY_SCHEDULED tasks whose backoff elapsed → QUEUED.
    * QUEUED tasks that may have lost their queue message (e.g. in-memory queue after restart) → re-enqueued.
    * PLANNING projects without a plan → re-enqueue planning.
    """
    now = _now()
    stats = {"expired_leases": 0, "retries_due": 0, "requeued": 0, "planning": 0}
    jobs: list[tuple[str, str, int]] = []
    plans: list[tuple[str, str]] = []
    projects: set[str] = set()
    async with session_scope() as s:
        for t in (await s.execute(select(Task).where(Task.status == TaskStatus.RUNNING, Task.lease_expires_at < now))).scalars():
            t.status, t.lease_owner, t.lease_expires_at = TaskStatus.QUEUED, None, None
            emit(s, t.org_id, EventType.TASK_RECOVERED, project_id=t.project_id, task_id=t.id, agent_key=t.agent_key,
                 message=f"Worker lease expired on '{t.title}'; re-queued to resume from checkpoint")
            jobs.append((t.id, t.org_id, t.priority))
            stats["expired_leases"] += 1
        for t in (await s.execute(select(Task).where(Task.status == TaskStatus.RETRY_SCHEDULED, Task.next_run_at <= now))).scalars():
            t.status = TaskStatus.QUEUED
            jobs.append((t.id, t.org_id, t.priority))
            stats["retries_due"] += 1
        cutoff = now - timedelta(seconds=stale_queued_after_s)
        # an empty queue with QUEUED tasks means messages were lost (restart of an in-memory queue)
        if await get_queue().depth() == 0:
            for t in (await s.execute(select(Task).where(Task.status == TaskStatus.QUEUED))).scalars():
                if (t.id, t.org_id, t.priority) not in jobs:
                    jobs.append((t.id, t.org_id, t.priority))
                    stats["requeued"] += 1
        for p in (await s.execute(select(Project).where(Project.status == ProjectStatus.PLANNING))).scalars():
            ts = p.started_at
            if ts and ts.replace(tzinfo=ts.tzinfo or UTC) < cutoff:
                plans.append((p.id, p.org_id))
                stats["planning"] += 1
        for p in (await s.execute(select(Project.id).where(Project.status.in_([ProjectStatus.RUNNING, ProjectStatus.WAITING_APPROVAL])))).scalars():
            projects.add(p)
    for tid, org, prio in jobs:
        await enqueue_task(tid, org, prio)
    for pid, org in plans:
        await enqueue_plan(pid, org)
    for pid in projects:
        await schedule(pid)
    return stats
