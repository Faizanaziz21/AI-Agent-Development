from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventType, emit
from app.models import Objective, Project, Workspace
from app.services import audit
from app.services.security.injection import sanitize

TEMPLATES = {
    "b2b_sales": {
        "name": "Autonomous B2B Sales Intelligence",
        "objective": "Find 100 qualified UK companies with 200–5,000 employees for our Enterprise Device Control Platform, "
                     "research each company, identify likely decision makers, score the opportunities, create personalised "
                     "outreach, and prepare the best 20 prospects for approval.",
        "parameters": {"product": "Enterprise Device Control Platform", "region": "United Kingdom", "country": "United Kingdom",
                       "min_employees": 200, "max_employees": 5000, "target_count": 100, "top_n": 20, "research_agents": 4,
                       "qualification_threshold": 40, "price_per_endpoint_month_usd": 6.0},
        "budget_usd": 50.0,
    },
    "competitive_research": {
        "name": "Competitive Landscape Report",
        "objective": "Research competitors in the endpoint device control market, collect pricing and prepare an executive report.",
        "parameters": {"market": "device control endpoint vendors"}, "budget_usd": 15.0,
    },
    "document_analysis": {
        "name": "Policy & Contract Analysis",
        "objective": "Summarise our refund policy and service level obligations and extract the key thresholds.",
        "parameters": {}, "budget_usd": 10.0,
    },
    "generic": {"name": "Custom Objective", "objective": "", "parameters": {}, "budget_usd": 20.0},
}


async def create_project(
    s: AsyncSession, *, org_id: str, user_id: str | None, name: str, objective: str, parameters: dict[str, Any] | None = None,
    template: str = "generic", budget_usd: float = 50.0, chaos: dict[str, Any] | None = None, description: str = "",
) -> Project:
    ws = (await s.execute(select(Workspace).where(Workspace.org_id == org_id).limit(1))).scalar_one_or_none()
    p = Project(org_id=org_id, workspace_id=ws.id if ws else None, name=sanitize(name)[:200], description=sanitize(description)[:2000],
                template=template, budget_usd=budget_usd, created_by=user_id, chaos=chaos or {})
    s.add(p)
    await s.flush()
    s.add(Objective(org_id=org_id, project_id=p.id, text=sanitize(objective)[:8000], parameters=parameters or {}))
    emit(s, org_id, EventType.PROJECT_CREATED, project_id=p.id, message=f"Project '{p.name}' created",
         payload={"template": template, "budget_usd": budget_usd})
    await audit.record(s, org_id, actor_type="user" if user_id else "system", actor_id=user_id or "system", action="project.created",
                       resource_type="project", resource_id=p.id, project_id=p.id, details={"template": template, "budget_usd": budget_usd})
    await s.flush()
    return p


DEMO_TICKETS = [
    {"customer_email": "tom.reid@quaysideretail.example", "subject": "USB barcode scanner blocked after policy change",
     "body": "Hi, since yesterday our USB barcode scanners at two stores are blocked by device control. The device is in the allow "
             "policy. How do we fix this? Thanks, Tom"},
    {"customer_email": "priya.shah@harbourhealthclinics.example", "subject": "Refund request — unused licences, very disappointed",
     "body": "This is unacceptable. We were billed for 400 extra endpoints we never deployed. I want a refund of $2,400 immediately "
             "or we will involve our solicitor. Invoice INV-20931."},
    {"customer_email": "grace.hall@kingswaycapital.example", "subject": "Keyboards stopped working after agent update",
     "body": "After updating the endpoint agent to 4.1 several USB keyboards stopped working. Is there a known issue?"},
]


async def create_support_ticket(
    s: AsyncSession, *, org_id: str, customer_email: str, subject: str, body: str, external_id: str = "",
    user_id: str | None = None, budget_usd: float = 5.0, chaos: dict[str, Any] | None = None,
) -> tuple[Project, Any]:
    from app.models import SupportTicket
    from app.services.security.injection import scan, wrap_untrusted

    t = SupportTicket(org_id=org_id, external_id=external_id[:120], customer_email=customer_email[:255],
                      subject=sanitize(subject)[:300], body=sanitize(body)[:20000], status="open",
                      attributes={"injection_risk": scan(body).risk})
    s.add(t)
    await s.flush()
    ticket = wrap_untrusted({"ticket_id": t.id, "subject": t.subject, "body": t.body, "customer_email": t.customer_email},
                            source="support_ticket")
    p = await create_project(s, org_id=org_id, user_id=user_id, name=f"Support: {t.subject[:80]}",
                             objective=f"Resolve support ticket {t.id}: {t.subject}", template="support_ticket",
                             parameters={"template": "support_ticket", "ticket": ticket}, budget_usd=budget_usd, chaos=chaos)
    t.project_id = p.id
    return p, t
