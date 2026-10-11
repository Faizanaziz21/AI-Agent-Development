from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_principal, get_owned, require, to_dict
from app.core.db import get_session
from app.core.security import Principal
from app.models import Entity, MemoryItem, Relationship, TrustLevel
from app.services import audit
from app.services import memory as memory_service

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/items")
async def list_items(layer: str | None = None, trust_level: str | None = None, project_id: str | None = None,
                     q: str | None = None, limit: int = Query(200, le=1000),
                     p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    if q:
        return await memory_service.retrieve(s, p.org_id, q, project_id=project_id, k=min(limit, 50))
    stmt = select(MemoryItem).where(MemoryItem.org_id == p.org_id)
    if layer:
        stmt = stmt.where(MemoryItem.layer == layer)
    if trust_level:
        stmt = stmt.where(MemoryItem.trust_level == trust_level)
    if project_id:
        stmt = stmt.where(MemoryItem.project_id == project_id)
    rows = (await s.execute(stmt.order_by(MemoryItem.created_at.desc()).limit(limit))).scalars()
    return [to_dict(m) for m in rows]


@router.get("/summary")
async def summary(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(MemoryItem.layer, MemoryItem.trust_level, func.count()).where(MemoryItem.org_id == p.org_id)
                            .group_by(MemoryItem.layer, MemoryItem.trust_level))).all()
    entities = dict((await s.execute(select(Entity.type, func.count()).where(Entity.org_id == p.org_id).group_by(Entity.type))).all())
    rels = (await s.execute(select(func.count()).select_from(Relationship).where(Relationship.org_id == p.org_id))).scalar_one()
    return {"items": [{"layer": la, "trust_level": t, "count": n} for la, t, n in rows], "entities": entities, "relationships": rels}


class HumanMemoryIn(BaseModel):
    content: str = Field(min_length=3, max_length=4000)
    kind: str = Field(default="fact", max_length=40)
    key: str = Field(default="", max_length=200)


@router.post("/items", status_code=201)
async def add_item(body: HumanMemoryIn, p: Principal = Depends(require("memory.approve")), s: AsyncSession = Depends(get_session)):
    item = await memory_service.write(s, p.org_id, layer="long_term", content=body.content, source_type="human",
                                      source_ref=f"user:{p.user_id}", kind=body.kind, key=body.key, confidence=1.0,
                                      trust_level=TrustLevel.APPROVED)
    item.approved_by = p.user_id
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="memory.created", resource_type="memory",
                       resource_id=item.id, details={"kind": body.kind})
    return to_dict(item)


class ReviewIn(BaseModel):
    promote: bool = True


@router.post("/items/{item_id}/approve")
async def approve(item_id: str, body: ReviewIn | None = None, p: Principal = Depends(require("memory.approve")),
                  s: AsyncSession = Depends(get_session)):
    item = await get_owned(s, MemoryItem, item_id, p)
    if item.trust_level == TrustLevel.REJECTED:
        raise HTTPException(409, "rejected memory cannot be approved; write a new item instead")
    await memory_service.approve(s, item, p.user_id, promote=(body or ReviewIn()).promote)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="memory.approved", resource_type="memory",
                       resource_id=item.id, project_id=item.project_id, details={"layer": item.layer})
    return to_dict(item)


@router.post("/items/{item_id}/reject")
async def reject(item_id: str, p: Principal = Depends(require("memory.approve")), s: AsyncSession = Depends(get_session)):
    item = await get_owned(s, MemoryItem, item_id, p)
    await memory_service.reject(s, item, p.user_id)
    await audit.record(s, p.org_id, actor_type="user", actor_id=p.user_id, action="memory.rejected", resource_type="memory",
                       resource_id=item.id, project_id=item.project_id)
    return to_dict(item)


@router.get("/entities")
async def entities(type: str | None = None, project_id: str | None = None, q: str | None = None, limit: int = Query(200, le=2000),
                   p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    stmt = select(Entity).where(Entity.org_id == p.org_id)
    if type:
        stmt = stmt.where(Entity.type == type)
    if project_id:
        stmt = stmt.where(Entity.project_id == project_id)
    if q:
        stmt = stmt.where(Entity.name.ilike(f"%{q}%"))
    rows = list((await s.execute(stmt.order_by(Entity.created_at.desc()).limit(limit))).scalars())
    ids = [e.id for e in rows]
    rels = []
    if ids:
        rels = list((await s.execute(select(Relationship).where(Relationship.org_id == p.org_id,
                                                                Relationship.source_entity_id.in_(ids)))).scalars())
    return {"entities": [to_dict(e) for e in rows], "relationships": [to_dict(r) for r in rels]}
