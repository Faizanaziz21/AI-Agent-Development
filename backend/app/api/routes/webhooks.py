from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip
from app.core.crypto import decrypt
from app.core.db import get_session
from app.core.security import verify_webhook
from app.models import Organization, SupportTicket
from app.services import audit, orchestrator
from app.services.projects import create_support_ticket

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

MAX_BODY = 256 * 1024


class SupportWebhook(BaseModel):
    external_id: str = Field(default="", max_length=120)
    customer_email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)


@router.post("/support/{org_slug}", status_code=202)
async def support_ticket(
    org_slug: str,
    request: Request,
    x_agentos_timestamp: str = Header(default=""),
    x_agentos_signature: str = Header(default=""),
    s: AsyncSession = Depends(get_session),
):
    """Inbound support ticket (helpdesk / email gateway). Signed with HMAC-SHA256 over `{timestamp}.{body}`."""
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "payload too large")
    org = (await s.execute(select(Organization).where(Organization.slug == org_slug))).scalar_one_or_none()
    # unknown org and bad signature are indistinguishable to the caller
    if org is None or not org.webhook_secret_enc or not verify_webhook(decrypt(org.webhook_secret_enc), raw,
                                                                       x_agentos_timestamp, x_agentos_signature):
        if org is not None:
            await audit.record(s, org.id, actor_type="system", actor_id="webhook", action="webhook.rejected",
                               resource_type="webhook", resource_id="support", ip=client_ip(request),
                               details={"reason": "invalid signature or stale timestamp"})
            await s.commit()
        raise HTTPException(401, "invalid signature")
    try:
        body = SupportWebhook.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(422, f"invalid payload: {exc}") from exc
    if body.external_id:
        dup = (await s.execute(select(SupportTicket).where(SupportTicket.org_id == org.id,
                                                           SupportTicket.external_id == body.external_id))).scalar_one_or_none()
        if dup is not None:
            return {"status": "duplicate", "ticket_id": dup.id, "project_id": dup.project_id}
    project, ticket = await create_support_ticket(s, org_id=org.id, customer_email=body.customer_email, subject=body.subject,
                                                  body=body.body, external_id=body.external_id)
    await audit.record(s, org.id, actor_type="system", actor_id="webhook", action="webhook.support_ticket", resource_type="ticket",
                       resource_id=ticket.id, project_id=project.id, ip=client_ip(request), details={"external_id": body.external_id})
    await s.commit()
    await orchestrator.start_project(project.id, "webhook")
    return {"status": "accepted", "ticket_id": ticket.id, "project_id": project.id}
