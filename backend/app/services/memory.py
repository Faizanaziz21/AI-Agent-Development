"""Memory Service.

Layers: working (task checkpoint, owned by the runtime), session (project), long_term
(organization), semantic (embedding retrieval over session + long-term items) and structured
(entities/relationships with per-attribute provenance).

Trust model: anything produced by an agent or tool is written as `unverified`. It becomes
`verified` only via an evaluator pass and `approved` (organizational knowledge) only via a human
with `memory.approve`. Retrieval always returns the trust level so prompts can label it, and
long-term retrieval returns approved items only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, MemoryItem, Relationship, TrustLevel
from app.services.rag.embeddings import get_embedder
from app.services.security.injection import sanitize


class MemoryError(Exception):
    pass


async def write(
    session: AsyncSession, org_id: str, *, layer: str, content: str, source_type: str, source_ref: str = "",
    project_id: str | None = None, task_id: str | None = None, agent_key: str | None = None,
    kind: str = "fact", key: str = "", data: dict | None = None, confidence: float = 0.5,
    trust_level: str | None = None,
) -> MemoryItem:
    if layer not in ("session", "long_term"):
        raise MemoryError(f"invalid layer {layer}")
    if layer == "session" and not project_id:
        raise MemoryError("session memory requires a project")
    trust = trust_level or TrustLevel.UNVERIFIED
    if trust == TrustLevel.APPROVED and source_type != "human":
        raise MemoryError("only humans can create approved memory")
    content = sanitize(content)[:4000]
    vec = (await get_embedder().embed([content]))[0]
    item = MemoryItem(
        org_id=org_id, project_id=project_id if layer == "session" else project_id, task_id=task_id, layer=layer,
        kind=kind, key=key[:200], content=content, data=data or {}, embedding=vec, trust_level=trust,
        source_type=source_type, source_ref=source_ref[:300], created_by_agent=agent_key,
        confidence=max(0.0, min(1.0, confidence)),
    )
    session.add(item)
    await session.flush()
    return item


async def retrieve(
    session: AsyncSession, org_id: str, query: str, *, project_id: str | None = None, k: int = 5,
    include_unverified: bool = True,
) -> list[dict[str, Any]]:
    conds = [MemoryItem.layer == "long_term"]
    if project_id:
        conds.append(MemoryItem.project_id == project_id)
    stmt = select(MemoryItem).where(MemoryItem.org_id == org_id, or_(*conds), MemoryItem.trust_level != TrustLevel.REJECTED)
    items = list((await session.execute(stmt.order_by(MemoryItem.created_at.desc()).limit(2000))).scalars())
    # long-term memory surfaces only human-approved knowledge
    items = [i for i in items if i.layer != "long_term" or i.trust_level == TrustLevel.APPROVED]
    if not include_unverified:
        items = [i for i in items if i.trust_level in (TrustLevel.VERIFIED, TrustLevel.APPROVED)]
    if not items:
        return []
    qv = np.array((await get_embedder().embed([query]))[0], dtype=np.float32)
    mat = np.array([i.embedding or np.zeros_like(qv) for i in items], dtype=np.float32)
    trust_boost = {TrustLevel.APPROVED: 0.15, TrustLevel.VERIFIED: 0.08}
    scores = mat @ qv + np.array([trust_boost.get(i.trust_level, 0.0) for i in items])
    out = []
    for idx in np.argsort(-scores)[:k]:
        it = items[int(idx)]
        out.append({"id": it.id, "layer": it.layer, "kind": it.kind, "content": it.content, "trust_level": it.trust_level,
                    "source_type": it.source_type, "source_ref": it.source_ref, "agent": it.created_by_agent,
                    "score": round(float(scores[idx]), 4), "created_at": it.created_at.isoformat() if it.created_at else None})
    return out


async def verify(session: AsyncSession, item: MemoryItem, by: str) -> MemoryItem:
    if item.trust_level == TrustLevel.UNVERIFIED:
        item.trust_level, item.verified_by = TrustLevel.VERIFIED, by
    return item


async def approve(session: AsyncSession, item: MemoryItem, user_id: str, promote: bool = True) -> MemoryItem:
    item.trust_level, item.approved_by, item.approved_at = TrustLevel.APPROVED, user_id, datetime.now(UTC)
    if promote:
        item.layer = "long_term"
    return item


async def reject(session: AsyncSession, item: MemoryItem, user_id: str) -> MemoryItem:
    item.trust_level, item.approved_by, item.approved_at = TrustLevel.REJECTED, user_id, datetime.now(UTC)
    return item


async def upsert_entity(
    session: AsyncSession, org_id: str, *, type: str, name: str, attributes: dict[str, Any],
    provenance: dict[str, Any], project_id: str | None = None,
) -> Entity:
    ent = (await session.execute(select(Entity).where(
        Entity.org_id == org_id, Entity.type == type, Entity.name == name, Entity.project_id == project_id
    ))).scalar_one_or_none()
    if ent is None:
        ent = Entity(org_id=org_id, project_id=project_id, type=type, name=name, attributes={}, provenance={})
        session.add(ent)
    attrs, prov = dict(ent.attributes or {}), dict(ent.provenance or {})
    for k, v in attributes.items():
        if v is None:
            continue
        new_conf = float(provenance.get("confidence", 0.5))
        old = prov.get(k)
        # keep the better-supported value; never let a lower-confidence source overwrite silently
        if old is None or new_conf >= float(old.get("confidence", 0)):
            attrs[k] = v
            prov[k] = {**provenance, "at": datetime.now(UTC).isoformat()}
    ent.attributes, ent.provenance = attrs, prov
    await session.flush()
    return ent


async def relate(session: AsyncSession, org_id: str, src: Entity, dst: Entity, type: str,
                 provenance: dict[str, Any], project_id: str | None = None) -> Relationship:
    existing = (await session.execute(select(Relationship).where(
        Relationship.org_id == org_id, Relationship.source_entity_id == src.id,
        Relationship.target_entity_id == dst.id, Relationship.type == type))).scalar_one_or_none()
    if existing:
        return existing
    rel = Relationship(org_id=org_id, project_id=project_id, source_entity_id=src.id, target_entity_id=dst.id,
                       type=type, provenance=provenance)
    session.add(rel)
    return rel
