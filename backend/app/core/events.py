"""Domain events.

Events are written to the `events` table inside the same transaction as the state change that
produced them (transactional outbox). After commit they are fanned out to in-process subscribers
and, when Redis is configured, published to `agentos:events:{org_id}` so API replicas and
notification workers in other processes receive them. Live streams fall back to tailing the table,
so delivery is at-least-once even without Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.governance import EventRecord

log = logging.getLogger("agentos.events")


class EventType:
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_STARTED = "PROJECT_STARTED"
    PLAN_CREATED = "PLAN_CREATED"
    TASK_CREATED = "TASK_CREATED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_QUEUED = "TASK_QUEUED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_STEP = "AGENT_STEP"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_DENIED = "TOOL_DENIED"
    MODEL_FALLBACK = "MODEL_FALLBACK"
    MODEL_DOWNGRADED = "MODEL_DOWNGRADED"
    TASK_DELEGATED = "TASK_DELEGATED"
    TASK_REVIEW_STARTED = "TASK_REVIEW_STARTED"
    TASK_REVISION_REQUESTED = "TASK_REVISION_REQUESTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_RETRY_SCHEDULED = "TASK_RETRY_SCHEDULED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_ESCALATED = "TASK_ESCALATED"
    TASK_REASSIGNED = "TASK_REASSIGNED"
    TASK_RECOVERED = "TASK_RECOVERED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_CHANGES_REQUESTED = "APPROVAL_CHANGES_REQUESTED"
    BUDGET_WARNING = "BUDGET_WARNING"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INJECTION_DETECTED = "INJECTION_DETECTED"
    MEMORY_WRITTEN = "MEMORY_WRITTEN"
    PROJECT_COMPLETED = "PROJECT_COMPLETED"
    PROJECT_FAILED = "PROJECT_FAILED"
    PROJECT_CANCELLED = "PROJECT_CANCELLED"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._redis = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_redis(self, client) -> None:
        self._redis = client

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subscribers[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subscribers[channel].discard(q)

    def dispatch(self, evt: dict[str, Any]) -> None:
        for channel in (f"org:{evt['org_id']}", f"project:{evt.get('project_id')}", "*"):
            for q in list(self._subscribers.get(channel, ())):
                try:
                    q.put_nowait(evt)
                except asyncio.QueueFull:
                    pass
        if self._redis is not None and self._loop is not None:
            payload = json.dumps(evt, default=str)
            self._loop.create_task(self._redis.publish(f"agentos:events:{evt['org_id']}", payload))


bus = EventBus()


def emit(
    session: AsyncSession,
    org_id: str,
    type: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    agent_key: str | None = None,
    message: str = "",
    payload: dict | None = None,
) -> EventRecord:
    rec = EventRecord(
        org_id=org_id, project_id=project_id, task_id=task_id, type=type,
        agent_key=agent_key, message=message[:2000], payload=payload or {},
    )
    session.add(rec)
    session.sync_session.info.setdefault("pending_events", []).append(rec)
    return rec


@sa_event.listens_for(Session, "after_commit")
def _after_commit(sess: Session) -> None:  # pragma: no cover - exercised indirectly
    pending = sess.info.pop("pending_events", [])
    for rec in pending:
        try:
            bus.dispatch(
                {
                    "id": rec.id, "org_id": rec.org_id, "project_id": rec.project_id, "task_id": rec.task_id,
                    "type": rec.type, "agent_key": rec.agent_key, "message": rec.message,
                    "payload": rec.payload, "created_at": rec.created_at.isoformat() if rec.created_at else None,
                }
            )
        except Exception:  # noqa: BLE001
            log.exception("event dispatch failed")


@sa_event.listens_for(Session, "after_rollback")
def _after_rollback(sess: Session) -> None:  # pragma: no cover
    sess.info.pop("pending_events", None)
