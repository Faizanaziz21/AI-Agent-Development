"""Business-system records used by the built-in (local) CRM, ticketing and messaging adapters.

Production deployments swap these adapters for Salesforce/HubSpot, Zendesk, SMTP/SES, Slack, etc.
via the adapter interfaces in services/tools/adapters.py; the tables remain as a system-of-record cache.
"""

from __future__ import annotations

from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TenantMixin


class CrmAccount(TenantMixin, Base):
    __tablename__ = "crm_accounts"

    name: Mapped[str] = mapped_column(String(300), index=True)
    domain: Mapped[str] = mapped_column(String(200), default="", index=True)
    industry: Mapped[str] = mapped_column(String(120), default="")
    employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    region: Mapped[str] = mapped_column(String(120), default="")
    lifecycle_stage: Mapped[str] = mapped_column(String(40), default="lead")  # lead|mql|sql|opportunity|customer
    tier: Mapped[str] = mapped_column(String(40), default="standard")
    owner: Mapped[str] = mapped_column(String(200), default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(120), default="manual")


class CrmContact(TenantMixin, Base):
    __tablename__ = "crm_contacts"

    account_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class CrmOpportunity(TenantMixin, Base):
    __tablename__ = "crm_opportunities"

    account_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    stage: Mapped[str] = mapped_column(String(40), default="qualification")
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class SupportTicket(TenantMixin, Base):
    __tablename__ = "support_tickets"

    external_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    category: Mapped[str] = mapped_column(String(80), default="")
    product_area: Mapped[str] = mapped_column(String(80), default="")
    risk_level: Mapped[str] = mapped_column(String(20), default="")
    resolution: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class OutboundMessage(TenantMixin, Base):
    """Outbox for email / Slack. Local adapter delivers here; production adapters forward to SMTP/Slack."""

    __tablename__ = "outbound_messages"

    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(20))  # email|slack
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|sent|failed
    recipient: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class AnalyticsEvent(TenantMixin, Base):
    __tablename__ = "analytics_events"

    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)


class StoredFile(TenantMixin, Base):
    __tablename__ = "stored_files"

    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    path: Mapped[str] = mapped_column(String(400), index=True)
    mime_type: Mapped[str] = mapped_column(String(120), default="text/plain")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str] = mapped_column(String(400))
    created_by: Mapped[str] = mapped_column(String(80), default="")
