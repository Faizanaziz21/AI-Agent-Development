from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db import session_scope
from app.models import Organization
from app.services import memory as memory_service
from app.services.policy.engine import decide, load_policies
from app.services.rag.retrieval import search as rag_search
from app.services.tools.base import PermissionLevel, Tool, ToolContext, register

CLASSIFICATIONS = ["public", "internal", "confidential", "legal"]


async def allowed_classifications(session, org_id: str, agent_key: str) -> set[str]:
    policies = await load_policies(session, org_id)
    return {c for c in CLASSIFICATIONS
            if decide(policies, "knowledge_access", {"agent_key": agent_key, "classification": c}).effect != "deny"}


class KnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    k: int = Field(default=5, ge=1, le=20)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    source_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


@register
class KnowledgeSearchTool(Tool):
    name = "knowledge_search"
    description = "Hybrid (semantic + keyword + metadata) search over the organization's knowledge base. Returns cited passages."
    category = "knowledge"
    input_model = KnowledgeSearchInput
    untrusted_output = True

    async def run(self, ctx: ToolContext, args: KnowledgeSearchInput) -> dict[str, Any]:
        async with session_scope() as s:
            allowed = await allowed_classifications(s, ctx.org_id, ctx.agent_key)
            passages = await rag_search(s, ctx.org_id, args.query, k=args.k, mode=args.mode,
                                        source_types=args.source_types or None, tags=args.tags or None,
                                        allowed_classifications=allowed)
        return {
            "query": args.query,
            "access_scope": sorted(allowed),
            # passages whose instruction-like content was already redacted at retrieval time
            "neutralised_passages": [f"S{i + 1}" for i, p in enumerate(passages) if p.injection_risk >= 0.5],
            "passages": [
                {**p.citation(i + 1), "text": p.text, "classification": p.classification, "source_type": p.source_type,
                 "semantic_rank": p.semantic_rank, "keyword_rank": p.keyword_rank,
                 "injection_risk": p.injection_risk}
                for i, p in enumerate(passages)
            ],
        }


class VectorSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    k: int = Field(default=5, ge=1, le=20)
    verified_only: bool = False


@register
class VectorSearchTool(Tool):
    name = "vector_search"
    description = "Semantic search over project and approved organizational memory. Results carry trust level and provenance."
    category = "memory"
    input_model = VectorSearchInput

    async def run(self, ctx: ToolContext, args: VectorSearchInput) -> dict[str, Any]:
        async with session_scope() as s:
            items = await memory_service.retrieve(s, ctx.org_id, args.query, project_id=ctx.project_id, k=args.k,
                                                  include_unverified=not args.verified_only)
        return {"items": items}


class MemoryWriteInput(BaseModel):
    content: str = Field(min_length=3, max_length=4000)
    kind: Literal["fact", "insight", "summary", "preference"] = "insight"
    key: str = Field(default="", max_length=200)
    scope: Literal["session", "long_term"] = "session"
    confidence: float = Field(default=0.6, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=20)


@register
class MemoryWriteTool(Tool):
    name = "memory_write"
    description = "Propose a memory item. Always stored as UNVERIFIED; long-term promotion needs human approval."
    category = "memory"
    permission_level = PermissionLevel.WRITE
    input_model = MemoryWriteInput

    async def run(self, ctx: ToolContext, args: MemoryWriteInput) -> dict[str, Any]:
        async with session_scope() as s:
            item = await memory_service.write(
                s, ctx.org_id, layer="session" if ctx.project_id else "long_term", content=args.content,
                source_type="agent", source_ref=f"task:{ctx.task_id}", project_id=ctx.project_id, task_id=ctx.task_id,
                agent_key=ctx.agent_key, kind=args.kind, key=args.key, confidence=args.confidence,
                data={"requested_scope": args.scope, "evidence": args.evidence},
            )
        return {"memory_id": item.id, "trust_level": item.trust_level,
                "note": "stored as unverified; promotion requires QA verification or human approval"}


class EntityFact(BaseModel):
    type: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=300)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class EntityUpsertInput(BaseModel):
    entities: list[EntityFact] = Field(min_length=1, max_length=200)
    relationships: list[dict[str, str]] = Field(default_factory=list, max_length=400)


@register
class EntityUpsertTool(Tool):
    name = "entity_upsert"
    description = "Record structured facts (entities with attributes and relationships) with provenance."
    category = "memory"
    permission_level = PermissionLevel.WRITE
    input_model = EntityUpsertInput

    async def run(self, ctx: ToolContext, args: EntityUpsertInput) -> dict[str, Any]:
        ids: dict[str, Any] = {}
        async with session_scope() as s:
            for e in args.entities:
                ent = await memory_service.upsert_entity(
                    s, ctx.org_id, type=e.type, name=e.name, attributes=e.attributes, project_id=ctx.project_id,
                    provenance={"source": e.source, "confidence": e.confidence, "agent": ctx.agent_key, "task_id": ctx.task_id})
                ids[e.name] = ent
            rels = 0
            for r in args.relationships:
                src, dst = ids.get(r.get("source", "")), ids.get(r.get("target", ""))
                if src and dst:
                    await memory_service.relate(s, ctx.org_id, src, dst, r.get("type", "related_to"),
                                                {"agent": ctx.agent_key, "task_id": ctx.task_id}, ctx.project_id)
                    rels += 1
        return {"entities": len(ids), "relationships": rels}


# ------------------------------------------------------------------ compliance
DEFAULT_COMM_RULES = {
    "require_opt_out": True,
    "require_sender_identity": True,
    "prohibited_phrases": ["guarantee", "100% secure", "risk-free", "act now", "you have been hacked", "final notice"],
    "blocked_recipient_domains": ["gmail.com", "hotmail.com", "yahoo.com", "outlook.com"],
    "max_recipients_per_batch": 50,
    "max_body_chars": 2500,
}


class ComplianceMessage(BaseModel):
    to: str
    subject: str
    body: str
    prospect_ref: str = ""


class PolicyCheckInput(BaseModel):
    messages: list[ComplianceMessage] = Field(min_length=1, max_length=500)
    channel: Literal["email", "slack", "public_post"] = "email"


@register
class PolicyCheckTool(Tool):
    name = "policy_check"
    description = "Check proposed communications against the organization's configurable communication rules."
    category = "compliance"
    input_model = PolicyCheckInput

    async def run(self, ctx: ToolContext, args: PolicyCheckInput) -> dict[str, Any]:
        async with session_scope() as s:
            org = (await s.execute(select(Organization).where(Organization.id == ctx.org_id))).scalar_one()
            rules = {**DEFAULT_COMM_RULES, **((org.settings or {}).get("communication_rules") or {})}
        results = []
        batch_violation = len(args.messages) > rules["max_recipients_per_batch"]
        for m in args.messages:
            v = []
            body_l = m.body.lower()
            if rules["require_opt_out"] and not re.search(r"unsubscribe|opt[- ]out|reply ['\"]?stop", body_l):
                v.append("missing opt-out mechanism")
            if rules["require_sender_identity"] and not re.search(r"\n\s*[-—]?\s*\w+.*\n.*(ltd|limited|plc|inc|agentos|team)", m.body, re.I):
                v.append("missing sender identity / company signature")
            for p in rules["prohibited_phrases"]:
                if p.lower() in body_l or p.lower() in m.subject.lower():
                    v.append(f"prohibited phrase: '{p}'")
            domain = m.to.split("@")[-1].lower()
            if domain in rules["blocked_recipient_domains"]:
                v.append(f"personal email domain not permitted for B2B outreach ({domain})")
            if len(m.body) > rules["max_body_chars"]:
                v.append("message too long")
            results.append({"to": m.to, "prospect_ref": m.prospect_ref, "passed": not v, "violations": v})
        passed = sum(1 for r in results if r["passed"])
        return {"rules_applied": sorted(k for k, val in rules.items() if val), "batch_size_violation": batch_violation,
                "passed": passed, "failed": len(results) - passed, "results": results}
