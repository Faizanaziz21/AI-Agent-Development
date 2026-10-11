from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_principal, get_owned, to_dict
from app.core.db import get_session
from app.core.security import Principal
from app.models import CrmAccount, CrmContact, CrmOpportunity, OutboundMessage, StoredFile, SupportTicket
from app.services.adapters import get_adapters

router = APIRouter(tags=["business data"])


@router.get("/crm/accounts")
async def accounts(limit: int = Query(200, le=2000), p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(CrmAccount).where(CrmAccount.org_id == p.org_id).order_by(CrmAccount.created_at.desc())
                            .limit(limit))).scalars()
    return [to_dict(a) for a in rows]


@router.get("/crm/contacts")
async def contacts(account_id: str | None = None, limit: int = Query(500, le=5000), p: Principal = Depends(current_principal),
                   s: AsyncSession = Depends(get_session)):
    q = select(CrmContact).where(CrmContact.org_id == p.org_id)
    if account_id:
        q = q.where(CrmContact.account_id == account_id)
    return [to_dict(c) for c in (await s.execute(q.limit(limit))).scalars()]


@router.get("/crm/opportunities")
async def opportunities(project_id: str | None = None, limit: int = Query(500, le=5000), p: Principal = Depends(current_principal),
                        s: AsyncSession = Depends(get_session)):
    q = select(CrmOpportunity, CrmAccount.name).join(CrmAccount, CrmAccount.id == CrmOpportunity.account_id, isouter=True) \
        .where(CrmOpportunity.org_id == p.org_id)
    if project_id:
        q = q.where(CrmOpportunity.project_id == project_id)
    rows = (await s.execute(q.order_by(CrmOpportunity.created_at.desc()).limit(limit))).all()
    return [to_dict(o, extra={"account_name": n}) for o, n in rows]


@router.get("/outbox")
async def outbox(project_id: str | None = None, channel: str | None = None, limit: int = Query(500, le=5000),
                 p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    q = select(OutboundMessage).where(OutboundMessage.org_id == p.org_id)
    if project_id:
        q = q.where(OutboundMessage.project_id == project_id)
    if channel:
        q = q.where(OutboundMessage.channel == channel)
    return [to_dict(m) for m in (await s.execute(q.order_by(OutboundMessage.created_at.desc()).limit(limit))).scalars()]


@router.get("/tickets")
async def tickets(limit: int = Query(200, le=2000), p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(SupportTicket).where(SupportTicket.org_id == p.org_id)
                            .order_by(SupportTicket.created_at.desc()).limit(limit))).scalars()
    return [to_dict(t) for t in rows]


@router.get("/files")
async def files(project_id: str | None = None, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    q = select(StoredFile).where(StoredFile.org_id == p.org_id)
    if project_id:
        q = q.where(StoredFile.project_id == project_id)
    return [to_dict(f) for f in (await s.execute(q.order_by(StoredFile.created_at.desc()).limit(500))).scalars()]


@router.get("/files/{file_id}/content")
async def file_content(file_id: str, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    f = await get_owned(s, StoredFile, file_id, p)
    if not f.storage_key.startswith(f"{p.org_id}/"):
        raise HTTPException(404, "file not found")
    data = get_adapters().storage.get(f.storage_key)
    return Response(data, media_type=f.mime_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{f.path.rsplit("/", 1)[-1]}"'})
