"""Approval Service: human-in-the-loop decisions with RBAC, payload editing and audit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventType, emit
from app.core.security import Principal, can_decide_approval
from app.models import Approval, ApprovalStatus
from app.services import audit
from app.services.tools.base import registry

DECISIONS = {"approve": ApprovalStatus.APPROVED, "reject": ApprovalStatus.REJECTED, "edit": ApprovalStatus.APPROVED,
             "request_changes": ApprovalStatus.CHANGES_REQUESTED}
VERBS = {"approve": "approved", "reject": "rejected", "edit": "approved with edits", "request_changes": "requested changes on"}


class ApprovalError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def decide(session: AsyncSession, approval: Approval, principal: Principal, decision: str,
                 edited_payload: dict[str, Any] | None = None, comment: str = "") -> Approval:
    if approval.org_id != principal.org_id:
        raise ApprovalError("approval not found", 404)
    if approval.status != ApprovalStatus.PENDING:
        raise ApprovalError(f"approval already {approval.status.lower()}", 409)
    if decision not in DECISIONS:
        raise ApprovalError(f"unknown decision '{decision}'")
    if not can_decide_approval(principal.role, approval.required_role):
        raise ApprovalError(f"requires role '{approval.required_role}'", 403)
    if decision == "request_changes" and not comment.strip():
        raise ApprovalError("a comment is required when requesting changes")
    if decision == "edit":
        if not edited_payload:
            raise ApprovalError("edited payload required")
        if approval.action_type == "tool" and approval.tool_name:
            tool = registry.get(approval.tool_name)
            try:
                edited_payload = tool.input_model.model_validate(edited_payload).model_dump(mode="json")
            except ValidationError as exc:
                raise ApprovalError("edited payload invalid: " + "; ".join(e["msg"] for e in exc.errors()[:5]), 422) from exc
        approval.edited_payload = edited_payload
    approval.status = DECISIONS[decision]
    approval.decided_by = principal.user_id
    approval.decided_at = datetime.now(UTC)
    approval.comment = comment[:4000]
    evt = {ApprovalStatus.APPROVED: EventType.APPROVAL_GRANTED, ApprovalStatus.REJECTED: EventType.APPROVAL_REJECTED,
           ApprovalStatus.CHANGES_REQUESTED: EventType.APPROVAL_CHANGES_REQUESTED}[approval.status]
    emit(session, approval.org_id, evt, project_id=approval.project_id, task_id=approval.task_id, agent_key=approval.agent_key,
         message=f"{principal.name or principal.email} {VERBS[decision]}: {approval.title}",
         payload={"approval_id": approval.id, "decision": decision, "edited": decision == "edit"})
    await audit.record(session, approval.org_id, actor_type="user", actor_id=principal.user_id, action=f"approval.{decision}",
                       resource_type="approval", resource_id=approval.id, project_id=approval.project_id,
                       details={"title": approval.title, "comment": comment[:500], "edited": decision == "edit"})
    return approval
