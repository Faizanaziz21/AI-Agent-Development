from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TenantMixin


class ApprovalStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    EXPIRED = "EXPIRED"


class Approval(TenantMixin, Base):
    __tablename__ = "approvals"

    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_key: Mapped[str] = mapped_column(String(80))
    action_type: Mapped[str] = mapped_column(String(60))  # tool|budget_extension|escalation|recommendation
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    edited_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    required_role: Mapped[str] = mapped_column(String(40), default="approver")
    policy_refs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default=ApprovalStatus.PENDING, index=True)
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")


class Policy(TenantMixin, Base):
    """Configurable guardrail rule evaluated by the policy engine on every tool call."""

    __tablename__ = "policies"

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # tool_call | knowledge_access | delegation
    scope: Mapped[str] = mapped_column(String(40), default="tool_call")
    # condition DSL — see services/policy/engine.py
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    # deny | require_approval | require_qa | allow
    effect: Mapped[str] = mapped_column(String(30))
    required_role: Mapped[str] = mapped_column(String(40), default="approver")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelUsage(TenantMixin, Base):
    __tablename__ = "model_usage"
    __table_args__ = (Index("ix_model_usage_org_created", "org_id", "created_at"),)

    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_key: Mapped[str] = mapped_column(String(80), default="")
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(40), default="reasoning")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_from: Mapped[str | None] = mapped_column(String(80), nullable=True)
    downgraded: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(TenantMixin, Base):
    """Append-only, hash-chained audit trail (per organization)."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_org_seq", "org_id", "seq"),)

    seq: Mapped[int] = mapped_column(Integer)
    actor_type: Mapped[str] = mapped_column(String(20))  # user|agent|system
    actor_id: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[str] = mapped_column(String(80), default="")
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class EventRecord(TenantMixin, Base):
    """Persisted domain events (outbox) — source for replay and live streams."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_project_created", "project_id", "created_at"),)

    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    agent_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(TenantMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
