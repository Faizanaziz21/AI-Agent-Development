from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TenantMixin


class TrustLevel:
    UNVERIFIED = "unverified"  # AI-produced, not checked
    VERIFIED = "verified"  # passed evaluator / cross-checked against sources
    APPROVED = "approved"  # human-approved organizational knowledge
    REJECTED = "rejected"


class MemoryItem(TenantMixin, Base):
    __tablename__ = "memory_items"

    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    layer: Mapped[str] = mapped_column(String(20), index=True)  # session|long_term|semantic
    kind: Mapped[str] = mapped_column(String(40), default="fact")  # fact|insight|preference|summary
    key: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    trust_level: Mapped[str] = mapped_column(String(20), default=TrustLevel.UNVERIFIED, index=True)
    source_type: Mapped[str] = mapped_column(String(20))  # agent|tool|document|human
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    created_by_agent: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    verified_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Entity(TenantMixin, Base):
    __tablename__ = "entities"

    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # per-attribute provenance: {"employees": {"source": "company_lookup", "confidence": 0.9, "task_id": ...}}
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    trust_level: Mapped[str] = mapped_column(String(20), default=TrustLevel.UNVERIFIED)


class Relationship(TenantMixin, Base):
    __tablename__ = "relationships"

    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    source_entity_id: Mapped[str] = mapped_column(String(32), ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    target_entity_id: Mapped[str] = mapped_column(String(32), ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(60))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeSource(TenantMixin, Base):
    __tablename__ = "knowledge_sources"

    name: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(40), default="document")  # policy|manual|faq|contract|sales|product
    filename: Mapped[str] = mapped_column(String(300), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="text/plain")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str] = mapped_column(String(400), default="")
    classification: Mapped[str] = mapped_column(String(30), default="internal")  # public|internal|confidential|legal
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")  # PENDING|INDEXED|FAILED
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    injection_flags: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeChunk(TenantMixin, Base):
    __tablename__ = "knowledge_chunks"

    source_id: Mapped[str] = mapped_column(String(32), ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    heading: Mapped[str] = mapped_column(String(300), default="")
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    injection_risk: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
