"""Audit Service — append-only, per-organization hash chain.

Each record's hash covers the previous hash plus the canonical JSON of the record, so any
modification or deletion of history is detectable by `verify_chain`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import AuditLog

GENESIS = "0" * 64


def _digest(prev_hash: str, rec: dict[str, Any]) -> str:
    body = json.dumps(rec, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def _canonical(log: AuditLog) -> dict[str, Any]:
    return {
        "seq": log.seq, "org_id": log.org_id, "actor_type": log.actor_type, "actor_id": log.actor_id,
        "action": log.action, "resource_type": log.resource_type, "resource_id": log.resource_id,
        "project_id": log.project_id, "details": log.details,
    }


async def record(
    session: AsyncSession,
    org_id: str,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    project_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "audit:" + org_id})
    last = (
        await session.execute(
            select(AuditLog.seq, AuditLog.hash).where(AuditLog.org_id == org_id).order_by(AuditLog.seq.desc()).limit(1)
        )
    ).first()
    # include not-yet-flushed audit rows from this same transaction
    pending = [o for o in session.new if isinstance(o, AuditLog) and o.org_id == org_id]
    if pending:
        tail = max(pending, key=lambda o: o.seq)
        seq, prev = tail.seq + 1, tail.hash
    elif last:
        seq, prev = last[0] + 1, last[1]
    else:
        seq, prev = 1, GENESIS
    log = AuditLog(
        org_id=org_id, seq=seq, actor_type=actor_type, actor_id=actor_id, action=action,
        resource_type=resource_type, resource_id=resource_id, project_id=project_id,
        details=details or {}, ip=ip, prev_hash=prev, hash="",
    )
    log.hash = _digest(prev, _canonical(log))
    session.add(log)
    return log


async def verify_chain(session: AsyncSession, org_id: str) -> dict[str, Any]:
    rows = (
        await session.execute(select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.seq))
    ).scalars().all()
    prev = GENESIS
    for i, r in enumerate(rows, start=1):
        if r.seq != i or r.prev_hash != prev or _digest(prev, _canonical(r)) != r.hash:
            return {"valid": False, "records": len(rows), "broken_at_seq": r.seq}
        prev = r.hash
    return {"valid": True, "records": len(rows), "head": prev}
