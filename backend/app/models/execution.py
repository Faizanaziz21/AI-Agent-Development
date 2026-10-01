from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TenantMixin


class ProjectStatus:
    DRAFT = "DRAFT"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus:
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_CHILDREN = "WAITING_CHILDREN"
    IN_REVIEW = "IN_REVIEW"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    TERMINAL = {COMPLETED, FAILED, CANCELLED}
    ACTIVE = {QUEUED, RUNNING, IN_REVIEW, RETRY_SCHEDULED}


class Project(TenantMixin, Base):
    __tablename__ = "projects"

    workspace_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("workspaces.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    template: Mapped[str] = mapped_column(String(80), default="generic")
    status: Mapped[str] = mapped_column(String(30), default=ProjectStatus.DRAFT, index=True)
    budget_usd: Mapped[float] = mapped_column(Float, default=50.0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    deliverable: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # fault-injection profile for resilience demos: {"provider_failure_rate": 0.2, ...}
    chaos: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Objective(TenantMixin, Base):
    __tablename__ = "objectives"

    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="OPEN")


class Plan(TenantMixin, Base):
    __tablename__ = "plans"

    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    objective_id: Mapped[str] = mapped_column(String(32), ForeignKey("objectives.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(Text, default="")
    strategy: Mapped[dict] = mapped_column(JSON, default=dict)
    graph: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_agent: Mapped[str] = mapped_column(String(80), default="supervisor")


class Task(TenantMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_project_status", "project_id", "status"),
        Index("ix_tasks_status_next_run", "status", "next_run_at"),
    )

    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("plans.id", ondelete="CASCADE"), nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("tasks.id"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    capability: Mapped[str] = mapped_column(String(80), default="generic")
    agent_key: Mapped[str] = mapped_column(String(80), index=True)
    agent_instance: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(30), default=TaskStatus.PENDING, index=True)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[str] = mapped_column(Text, default="")
    current_action: Mapped[str] = mapped_column(String(300), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    delegation_depth: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    budget_usd: Mapped[float] = mapped_column(Float, default=2.0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_criteria: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # revision feedback from evaluators / humans: [{"source": "qa"|"human", "text": ..., "issues": [...]}]
    feedback: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Execution(TenantMixin, Base):
    """One attempt of one task by one agent."""

    __tablename__ = "executions"

    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    agent_key: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    worker_id: Mapped[str] = mapped_column(String(80), default="")
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    models_used: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionStep(TenantMixin, Base):
    """Safe execution summary — never raw chain-of-thought."""

    __tablename__ = "execution_steps"

    project_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    execution_id: Mapped[str] = mapped_column(String(32), ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    agent_key: Mapped[str] = mapped_column(String(80))
    seq: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(80), default="")
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_action: Mapped[str] = mapped_column(String(200), default="")
    flags: Mapped[list] = mapped_column(JSON, default=list)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class ToolCall(TenantMixin, Base):
    __tablename__ = "tool_calls"

    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_key: Mapped[str] = mapped_column(String(80))
    tool_name: Mapped[str] = mapped_column(String(80), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30))  # SUCCESS|ERROR|DENIED|APPROVAL_REQUIRED|TIMEOUT
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    policy_decision: Mapped[dict] = mapped_column(JSON, default=dict)


class Evaluation(TenantMixin, Base):
    __tablename__ = "evaluations"

    project_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    evaluator_agent: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    overall: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    feedback: Mapped[str] = mapped_column(Text, default="")
    issues: Mapped[list] = mapped_column(JSON, default=list)


class TaskRevision(TenantMixin, Base):
    __tablename__ = "task_revisions"

    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str] = mapped_column(Text, default="")
