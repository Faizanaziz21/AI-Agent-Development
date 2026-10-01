"""Idempotent seeding of organizations, users, the default workforce, tools, policies and demo data."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.core.security import hash_password
from app.models import (
    AgentDefinition,
    CrmAccount,
    CrmContact,
    KnowledgeSource,
    Organization,
    Policy,
    ToolDefinition,
    User,
    Workspace,
)
from app.seed.agents import AGENTS, POLICIES
from app.seed.datasets import company_directory
from app.services import audit
from app.services.adapters import get_adapters
from app.services.rag.retrieval import ingest
from app.services.tools import builtin  # noqa: F401
from app.services.tools.base import registry

log = logging.getLogger("agentos.seed")
DEMO_PASSWORD = "AgentOS!2026"
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
KNOWLEDGE = [
    ("product-overview.md", "Product Overview — Device Control Platform", "product", "public", ["product", "sales"]),
    ("sales-playbook.md", "Sales Playbook", "sales", "internal", ["sales"]),
    ("admin-guide-usb.md", "Admin Guide — Blocked USB Devices", "manual", "internal", ["support", "usb"]),
    ("intune-integration.md", "Intune Integration Guide", "manual", "internal", ["support", "integration"]),
    ("refund-policy.md", "Refund and Credit Policy", "policy", "internal", ["support", "billing"]),
    ("sso-faq.md", "FAQ — Single Sign-On", "faq", "public", ["support", "sso"]),
    ("master-services-agreement.md", "Master Services Agreement", "contract", "legal", ["legal"]),
    ("community-forum-post.md", "Community Forum Thread (imported)", "faq", "public", ["support", "usb", "community"]),
]
TOOL_CONFIG = {"http_request": {"allowlist": ["api.github.com", "api.company-information.service.gov.uk", "httpbin.org"]}}


async def seed_tools(s: AsyncSession, org_id: str) -> None:
    existing = {t.name for t in (await s.execute(select(ToolDefinition).where(ToolDefinition.org_id == org_id))).scalars()}
    for t in registry.all():
        if t.name in existing:
            continue
        s.add(ToolDefinition(org_id=org_id, name=t.name, description=t.description, category=t.category,
                             input_schema=t.input_schema(), output_schema=t.output_description, permission_level=t.permission_level,
                             timeout_seconds=t.timeout_seconds, retry_policy=t.retry_policy, requires_approval=t.requires_approval,
                             implementation=f"builtin:{t.name}", config=TOOL_CONFIG.get(t.name, {})))


async def seed_workforce(s: AsyncSession, org_id: str) -> None:
    existing = {a.key for a in (await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == org_id))).scalars()}
    for a in AGENTS:
        if a["key"] in existing:
            continue
        s.add(AgentDefinition(org_id=org_id, is_builtin=True, **{
            "max_iterations": 8, "cost_budget_usd": 2.0, "token_budget": 200000, "prohibited_tools": [], "model_policy": {},
            "memory_config": {"session": True, "long_term": True, "semantic_top_k": 3}, "escalation_rules": {"on_failure": "human"},
            "approval_rules": [], "can_delegate_to": [], **a}))
    if not (await s.execute(select(Policy.id).where(Policy.org_id == org_id).limit(1))).first():
        for p in POLICIES:
            s.add(Policy(org_id=org_id, scope=p.get("scope", "tool_call"), required_role=p.get("required_role", "approver"),
                         **{k: v for k, v in p.items() if k not in ("scope", "required_role")}))


async def seed_knowledge(s: AsyncSession, org_id: str) -> None:
    if (await s.execute(select(KnowledgeSource.id).where(KnowledgeSource.org_id == org_id).limit(1))).first():
        return
    storage = get_adapters().storage
    for fname, name, stype, classification, tags in KNOWLEDGE:
        data = (KNOWLEDGE_DIR / fname).read_bytes()
        src = KnowledgeSource(org_id=org_id, name=name, source_type=stype, filename=fname, mime_type="text/markdown",
                              size_bytes=len(data), classification=classification, tags=tags, status="PENDING")
        s.add(src)
        await s.flush()
        src.storage_key = storage.put(f"{org_id}/knowledge/{src.id}/{fname}", data)
        await ingest(s, src, data)


async def seed_crm(s: AsyncSession, org_id: str) -> None:
    if (await s.execute(select(CrmAccount.id).where(CrmAccount.org_id == org_id).limit(1))).first():
        return
    customers = [
        ("Harbour Health Clinics Ltd", "harbourhealthclinics.example", "Healthcare", 1800, "enterprise", 186000,
         [("Priya Shah", "IT Director", "priya.shah@harbourhealthclinics.example")]),
        ("Quayside Retail Group", "quaysideretail.example", "Retail", 950, "standard", 42000,
         [("Tom Reid", "IT Manager", "tom.reid@quaysideretail.example")]),
        ("Kingsway Capital plc", "kingswaycapital.example", "Financial Services", 2600, "enterprise", 240000,
         [("Grace Hall", "CISO", "grace.hall@kingswaycapital.example")]),
    ]
    for name, domain, ind, emp, tier, arr, contacts in customers:
        a = CrmAccount(org_id=org_id, name=name, domain=domain, industry=ind, employees=emp, region="London",
                       lifecycle_stage="customer", tier=tier, owner="Jamie Fox", attributes={"arr_usd": arr}, source="seed")
        s.add(a)
        await s.flush()
        for cn, title, email in contacts:
            s.add(CrmContact(org_id=org_id, account_id=a.id, name=cn, title=title, email=email))
    # a few existing leads that overlap with the prospect universe → exercises duplicate detection
    fs = [c for c in company_directory() if c["country"] == "United Kingdom" and c["industry"] in ("Financial Services", "Healthcare")
          and c["employees"] and 800 <= c["employees"] <= 4000][:6]
    for c in fs:
        s.add(CrmAccount(org_id=org_id, name=c["name"].replace(" Ltd", " Limited"), domain=c["domain"], industry=c["industry"],
                         employees=c["employees"], region=c["region"], lifecycle_stage="lead", source="seed:legacy-import"))


async def seed_org(s: AsyncSession, *, name: str, slug: str, users: list[tuple[str, str, str, str]], demo: bool = True,
                   budget: float = 500.0) -> Organization:
    org = (await s.execute(select(Organization).where(Organization.slug == slug))).scalar_one_or_none()
    if org is None:
        org = Organization(name=name, slug=slug, monthly_budget_usd=budget,
                           webhook_secret_enc=encrypt(f"whsec_{slug}_demo_secret"),
                           settings={"quality_threshold": 0.75, "default_project_budget_usd": 50.0})
        s.add(org)
        await s.flush()
        s.add(Workspace(org_id=org.id, name="Default", description="Default workspace"))
        for email, uname, role, title in users:
            s.add(User(org_id=org.id, email=email, name=uname, role=role, title=title, password_hash=hash_password(DEMO_PASSWORD)))
        await audit.record(s, org.id, actor_type="system", actor_id="seed", action="org.created", resource_type="organization", resource_id=org.id)
    await seed_tools(s, org.id)
    await s.flush()
    await seed_workforce(s, org.id)
    if demo:
        await seed_knowledge(s, org.id)
        await seed_crm(s, org.id)
    return org


async def seed_all(s: AsyncSession) -> None:
    await seed_org(s, name="Sentinel Devices Ltd", slug="sentinel", users=[
        ("admin@sentinel.example", "Morgan Blake", "owner", "Head of Revenue Operations"),
        ("ops@sentinel.example", "Sam Carter", "operator", "Sales Operations Lead"),
        ("approver@sentinel.example", "Jordan Lee", "approver", "Sales Director"),
        ("finance@sentinel.example", "Alex Kim", "finance_manager", "Finance Manager"),
        ("compliance@sentinel.example", "Riley Ahmed", "compliance_officer", "Compliance Officer"),
        ("viewer@sentinel.example", "Casey Wong", "viewer", "Analyst"),
    ])
    await seed_org(s, name="Northwind Health", slug="northwind", demo=False, users=[
        ("admin@northwind.example", "Dana Smith", "owner", "COO"),
    ])
