from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select

from app.core.db import session_scope
from app.models import AnalyticsEvent, CrmAccount, CrmContact, CrmOpportunity, OutboundMessage, SupportTicket
from app.services.adapters import get_adapters
from app.services.tools.base import PermissionLevel, Tool, ToolContext, ToolValidationError, register

_LEGAL_SUFFIX = re.compile(r"\b(ltd|limited|plc|group|holdings|llp|inc)\b\.?", re.I)


def normalize_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _LEGAL_SUFFIX.sub("", name.lower()))


def _account_dict(a: CrmAccount) -> dict:
    return {"id": a.id, "name": a.name, "domain": a.domain, "industry": a.industry, "employees": a.employees,
            "region": a.region, "lifecycle_stage": a.lifecycle_stage, "tier": a.tier, "owner": a.owner,
            "attributes": a.attributes}


# ------------------------------------------------------------------ CRM
class CrmSearchInput(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=10, ge=1, le=100)


@register
class CrmSearchTool(Tool):
    name = "crm_search"
    description = "Search CRM accounts by name, domain or contact email; includes contacts and open opportunities."
    category = "crm"
    input_model = CrmSearchInput

    async def run(self, ctx: ToolContext, args: CrmSearchInput) -> dict[str, Any]:
        async with session_scope() as s:
            stmt = select(CrmAccount).where(CrmAccount.org_id == ctx.org_id)
            domain = args.domain or (args.email.split("@")[-1].lower() if args.email else None)
            if domain:
                stmt = stmt.where(CrmAccount.domain == domain)
            elif args.query:
                stmt = stmt.where(CrmAccount.name.ilike(f"%{args.query}%"))
            accounts = list((await s.execute(stmt.limit(args.limit))).scalars())
            out = []
            for a in accounts:
                contacts = (await s.execute(select(CrmContact).where(CrmContact.org_id == ctx.org_id, CrmContact.account_id == a.id))).scalars()
                opps = (await s.execute(select(CrmOpportunity).where(CrmOpportunity.org_id == ctx.org_id, CrmOpportunity.account_id == a.id))).scalars()
                d = _account_dict(a)
                d["contacts"] = [{"name": c.name, "title": c.title, "email": c.email} for c in contacts]
                d["opportunities"] = [{"id": o.id, "name": o.name, "stage": o.stage, "amount_usd": o.amount_usd} for o in opps]
                out.append(d)
        return {"count": len(out), "accounts": out}


class OpportunityRecord(BaseModel):
    company_name: str = Field(min_length=1, max_length=300)
    domain: str = Field(min_length=3, max_length=200)
    industry: str = ""
    employees: int | None = None
    region: str = ""
    score: float = Field(default=0, ge=0, le=100)
    amount_usd: float = Field(default=0, ge=0, le=10_000_000)
    contact_name: str = ""
    contact_title: str = ""
    contact_email: str = ""
    notes: str = Field(default="", max_length=4000)


class BulkOpportunityInput(BaseModel):
    records: list[OpportunityRecord] = Field(min_length=1, max_length=200)
    stage: Literal["qualification", "discovery", "proposal"] = "qualification"


@register
class CrmBulkCreateOpportunitiesTool(Tool):
    name = "crm_bulk_create_opportunities"
    description = "Create/update CRM accounts (with duplicate detection), set lifecycle stage and create opportunities."
    category = "crm"
    permission_level = PermissionLevel.WRITE
    input_model = BulkOpportunityInput
    timeout_seconds = 30.0

    def facts(self, args: BulkOpportunityInput) -> dict[str, Any]:
        return {"record_count": len(args.records), "total_amount_usd": sum(r.amount_usd for r in args.records),
                "changes_opportunities": True}

    def approval_request(self, args: BulkOpportunityInput) -> tuple[str, dict[str, Any]]:
        return (f"Create {len(args.records)} CRM opportunities", args.model_dump())

    async def run(self, ctx: ToolContext, args: BulkOpportunityInput) -> dict[str, Any]:
        created, updated, duplicates, opps = 0, 0, [], []
        async with session_scope() as s:
            existing = list((await s.execute(select(CrmAccount).where(CrmAccount.org_id == ctx.org_id))).scalars())
            by_domain = {a.domain: a for a in existing if a.domain}
            by_name = {normalize_company(a.name): a for a in existing}
            for r in args.records:
                acct = by_domain.get(r.domain.lower()) or by_name.get(normalize_company(r.company_name))
                if acct is None:
                    acct = CrmAccount(org_id=ctx.org_id, name=r.company_name, domain=r.domain.lower(), industry=r.industry,
                                      employees=r.employees, region=r.region, lifecycle_stage="opportunity",
                                      source=f"agentos:{ctx.project_id}", attributes={"score": r.score})
                    s.add(acct)
                    await s.flush()
                    by_domain[acct.domain] = acct
                    created += 1
                else:
                    duplicates.append({"company": r.company_name, "matched_account": acct.name, "account_id": acct.id})
                    acct.employees = acct.employees or r.employees
                    acct.industry = acct.industry or r.industry
                    if acct.lifecycle_stage in ("lead", "mql", "sql"):
                        acct.lifecycle_stage = "opportunity"
                    updated += 1
                if r.contact_email:
                    has = (await s.execute(select(CrmContact).where(CrmContact.org_id == ctx.org_id, CrmContact.email == r.contact_email))).scalar_one_or_none()
                    if not has:
                        s.add(CrmContact(org_id=ctx.org_id, account_id=acct.id, name=r.contact_name, title=r.contact_title, email=r.contact_email))
                open_opp = (await s.execute(select(CrmOpportunity).where(
                    CrmOpportunity.org_id == ctx.org_id, CrmOpportunity.account_id == acct.id,
                    CrmOpportunity.stage.notin_(["closed_won", "closed_lost"])))).scalar_one_or_none()
                if open_opp:
                    open_opp.score = max(open_opp.score, r.score)
                    opps.append({"id": open_opp.id, "account": acct.name, "status": "existing"})
                    continue
                opp = CrmOpportunity(org_id=ctx.org_id, account_id=acct.id, project_id=ctx.project_id,
                                     name=f"{acct.name} — Device Control", stage=args.stage, amount_usd=r.amount_usd,
                                     score=r.score, notes=r.notes)
                s.add(opp)
                await s.flush()
                opps.append({"id": opp.id, "account": acct.name, "status": "created"})
        return {"accounts_created": created, "accounts_updated": updated, "duplicates_detected": duplicates,
                "opportunities": opps, "opportunities_created": sum(1 for o in opps if o["status"] == "created")}


class UpdateOpportunityInput(BaseModel):
    opportunity_id: str
    stage: str | None = None
    amount_usd: float | None = Field(default=None, ge=0)
    notes: str | None = None


@register
class CrmUpdateOpportunityTool(Tool):
    name = "crm_update_opportunity"
    description = "Update stage/amount/notes of an existing CRM opportunity."
    category = "crm"
    permission_level = PermissionLevel.WRITE
    input_model = UpdateOpportunityInput

    def facts(self, args: UpdateOpportunityInput) -> dict[str, Any]:
        return {"changes_opportunities": True, "amount_usd": args.amount_usd or 0}

    async def run(self, ctx: ToolContext, args: UpdateOpportunityInput) -> dict[str, Any]:
        async with session_scope() as s:
            opp = (await s.execute(select(CrmOpportunity).where(CrmOpportunity.org_id == ctx.org_id, CrmOpportunity.id == args.opportunity_id))).scalar_one_or_none()
            if not opp:
                raise ToolValidationError("opportunity not found")
            for f in ("stage", "amount_usd", "notes"):
                if getattr(args, f) is not None:
                    setattr(opp, f, getattr(args, f))
            return {"id": opp.id, "stage": opp.stage, "amount_usd": opp.amount_usd}


class DeleteRecordInput(BaseModel):
    record_type: Literal["account", "contact", "opportunity"]
    record_id: str


@register
class CrmDeleteRecordTool(Tool):
    name = "crm_delete_record"
    description = "Delete a CRM record. Destructive — always requires human approval."
    category = "crm"
    permission_level = PermissionLevel.PRIVILEGED
    requires_approval = True
    input_model = DeleteRecordInput

    async def run(self, ctx: ToolContext, args: DeleteRecordInput) -> dict[str, Any]:
        model = {"account": CrmAccount, "contact": CrmContact, "opportunity": CrmOpportunity}[args.record_type]
        async with session_scope() as s:
            obj = (await s.execute(select(model).where(model.org_id == ctx.org_id, model.id == args.record_id))).scalar_one_or_none()
            if not obj:
                raise ToolValidationError("record not found")
            await s.delete(obj)
        return {"deleted": True, "record_type": args.record_type, "record_id": args.record_id}


# ------------------------------------------------------------------ messaging
class EmailMessageIn(BaseModel):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    prospect_ref: str = ""

    @field_validator("body")
    @classmethod
    def _no_script(cls, v: str) -> str:
        if re.search(r"<script|javascript:", v, re.I):
            raise ValueError("active content is not allowed in email bodies")
        return v


class EmailDraftInput(BaseModel):
    messages: list[EmailMessageIn] = Field(min_length=1, max_length=200)


@register
class EmailDraftTool(Tool):
    name = "email_draft"
    description = "Save email drafts for review. Does not send anything."
    category = "communication"
    permission_level = PermissionLevel.WRITE
    input_model = EmailDraftInput

    async def run(self, ctx: ToolContext, args: EmailDraftInput) -> dict[str, Any]:
        ids = []
        async with session_scope() as s:
            for m in args.messages:
                msg = OutboundMessage(org_id=ctx.org_id, project_id=ctx.project_id, channel="email", status="draft",
                                      recipient=m.to, subject=m.subject, body=m.body, meta={"prospect_ref": m.prospect_ref, "task_id": ctx.task_id})
                s.add(msg)
                await s.flush()
                ids.append(msg.id)
        return {"drafts_created": len(ids), "draft_ids": ids}


class EmailSendInput(BaseModel):
    messages: list[EmailMessageIn] = Field(min_length=1, max_length=500)
    risk_level: Literal["low", "medium", "high"] = "medium"
    purpose: Literal["outreach", "support_reply", "notification"] = "notification"


@register
class EmailSendTool(Tool):
    name = "email_send"
    description = "Send external email. Subject to approval, QA and recipient policies."
    category = "communication"
    permission_level = PermissionLevel.EXTERNAL
    input_model = EmailSendInput
    sensitive_args = ("messages.to",)
    timeout_seconds = 30.0

    def facts(self, args: EmailSendInput) -> dict[str, Any]:
        return {"recipient_count": len({m.to.lower() for m in args.messages}), "external": True,
                "risk_level": args.risk_level, "purpose": args.purpose}

    def approval_request(self, args: EmailSendInput) -> tuple[str, dict[str, Any]]:
        n = len(args.messages)
        noun = "personalized prospect emails" if args.purpose == "outreach" else "emails"
        return (f"Send {n} {noun}" if n > 1 else f"Send email to {args.messages[0].to}", args.model_dump())

    async def run(self, ctx: ToolContext, args: EmailSendInput) -> dict[str, Any]:
        sender = get_adapters().email
        sent = []
        async with session_scope() as s:
            for m in args.messages:
                res = await sender.send(m.to, m.subject, m.body, {"project_id": ctx.project_id})
                s.add(OutboundMessage(org_id=ctx.org_id, project_id=ctx.project_id, channel="email", status="sent",
                                      recipient=m.to, subject=m.subject, body=m.body,
                                      meta={"transport": res.get("transport"), "purpose": args.purpose, "task_id": ctx.task_id}))
                sent.append(m.to)
        return {"sent": len(sent), "recipients": sent}


class SlackInput(BaseModel):
    channel: str = Field(pattern=r"^#?[a-z0-9_\-]{1,80}$")
    text: str = Field(min_length=1, max_length=4000)


@register
class SlackMessageTool(Tool):
    name = "slack_message"
    description = "Post a message to an internal Slack channel."
    category = "communication"
    permission_level = PermissionLevel.WRITE
    input_model = SlackInput

    async def run(self, ctx: ToolContext, args: SlackInput) -> dict[str, Any]:
        res = await get_adapters().slack.post(args.channel, args.text)
        async with session_scope() as s:
            s.add(OutboundMessage(org_id=ctx.org_id, project_id=ctx.project_id, channel="slack", status="sent",
                                  recipient=args.channel, body=args.text, meta=res))
        return {"posted": True, "channel": args.channel}


# ------------------------------------------------------------------ support
class TicketUpdateInput(BaseModel):
    ticket_id: str
    status: Literal["open", "triaged", "pending_customer", "pending_approval", "resolved", "escalated"] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    category: str | None = Field(default=None, max_length=80)
    product_area: str | None = Field(default=None, max_length=80)
    risk_level: str | None = Field(default=None, max_length=20)
    resolution: str | None = Field(default=None, max_length=8000)
    summary: str | None = Field(default=None, max_length=4000)


@register
class TicketUpdateTool(Tool):
    name = "ticket_update"
    description = "Update a support ticket's status, classification, resolution and summary."
    category = "support"
    permission_level = PermissionLevel.WRITE
    input_model = TicketUpdateInput

    async def run(self, ctx: ToolContext, args: TicketUpdateInput) -> dict[str, Any]:
        async with session_scope() as s:
            t = (await s.execute(select(SupportTicket).where(SupportTicket.org_id == ctx.org_id, SupportTicket.id == args.ticket_id))).scalar_one_or_none()
            if not t:
                raise ToolValidationError("ticket not found")
            changed = {}
            for f in ("status", "priority", "category", "product_area", "risk_level", "resolution", "summary"):
                v = getattr(args, f)
                if v is not None:
                    setattr(t, f, v)
                    changed[f] = v if len(str(v)) < 80 else str(v)[:77] + "..."
            return {"ticket_id": t.id, "updated": changed}


class AnalyticsInput(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,80}$")
    properties: dict[str, Any] = Field(default_factory=dict)


@register
class AnalyticsEventTool(Tool):
    name = "analytics_event"
    description = "Emit a product analytics event."
    category = "analytics"
    permission_level = PermissionLevel.WRITE
    input_model = AnalyticsInput

    async def run(self, ctx: ToolContext, args: AnalyticsInput) -> dict[str, Any]:
        async with session_scope() as s:
            s.add(AnalyticsEvent(org_id=ctx.org_id, project_id=ctx.project_id, name=args.name, properties=args.properties))
        return {"recorded": True, "name": args.name}


class RefundInput(BaseModel):
    ticket_id: str | None = None
    account_email: EmailStr
    amount_usd: float = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=3, max_length=1000)


@register
class RefundIssueTool(Tool):
    name = "refund_issue"
    description = "Issue a customer refund through the billing system."
    category = "finance"
    permission_level = PermissionLevel.PRIVILEGED
    input_model = RefundInput
    sensitive_args = ("account_email",)

    def facts(self, args: RefundInput) -> dict[str, Any]:
        return {"amount_usd": args.amount_usd, "financial": True}

    def approval_request(self, args: RefundInput) -> tuple[str, dict[str, Any]]:
        return (f"Issue ${args.amount_usd:,.2f} refund to {args.account_email}", args.model_dump())

    async def run(self, ctx: ToolContext, args: RefundInput) -> dict[str, Any]:
        async with session_scope() as s:
            n = (await s.execute(select(func.count(AnalyticsEvent.id)).where(AnalyticsEvent.org_id == ctx.org_id, AnalyticsEvent.name == "billing.refund_issued"))).scalar_one()
            refund_id = f"rf_{n + 1:05d}"
            s.add(AnalyticsEvent(org_id=ctx.org_id, project_id=ctx.project_id, name="billing.refund_issued",
                                 properties={"refund_id": refund_id, "amount_usd": args.amount_usd, "email": args.account_email, "ticket_id": args.ticket_id}))
        return {"refund_id": refund_id, "amount_usd": args.amount_usd, "status": "issued"}

