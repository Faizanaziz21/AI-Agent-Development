from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TenantMixin


class AgentDefinition(TenantMixin, Base):
    __tablename__ = "agent_definitions"
    __table_args__ = (UniqueConstraint("org_id", "key"),)

    key: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(80))
    system_instructions: Mapped[str] = mapped_column(Text, default="")
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    prohibited_tools: Mapped[list] = mapped_column(JSON, default=list)
    # {"tier": "standard|advanced|economy", "model": optional pinned model, "fallbacks": [...]}
    model_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    max_iterations: Mapped[int] = mapped_column(Integer, default=8)
    token_budget: Mapped[int] = mapped_column(Integer, default=60000)
    cost_budget_usd: Mapped[float] = mapped_column(Float, default=2.0)
    # {"session": true, "long_term": true, "semantic_top_k": 4, "write_session": true}
    memory_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"on_failure": "supervisor|human", "max_retries": 2, "alternate_agent": "key"}
    escalation_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    # [{"tool": "email_send", "require": "approval", "role": "approver"}]
    approval_rules: Mapped[list] = mapped_column(JSON, default=list)
    can_delegate_to: Mapped[list] = mapped_column(JSON, default=list)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    color: Mapped[str] = mapped_column(String(20), default="#6366f1")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ToolDefinition(TenantMixin, Base):
    """Per-organization configuration of a registered tool implementation."""

    __tablename__ = "tool_definitions"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    name: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(60), default="general")
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    permission_level: Mapped[str] = mapped_column(String(20), default="read")
    timeout_seconds: Mapped[float] = mapped_column(Float, default=15.0)
    # {"max_attempts": 3, "backoff_seconds": 0.5, "backoff_multiplier": 2.0, "retry_on": ["timeout","transient"]}
    retry_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    implementation: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
