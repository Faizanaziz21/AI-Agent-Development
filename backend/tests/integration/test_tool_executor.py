"""Tool permission, policy, approval gate, retry, taint and injection handling in the ToolExecutor."""

from sqlalchemy import select

from app.core.db import session_scope
from app.models import AgentDefinition, Approval, ToolCall, ToolDefinition
from app.services.agents.profile import AgentProfile
from app.services.tools.base import ToolContext
from app.services.tools.executor import executor


async def agent(org_id: str, key: str) -> AgentProfile:
    async with session_scope() as s:
        row = (await s.execute(select(AgentDefinition).where(AgentDefinition.org_id == org_id, AgentDefinition.key == key))).scalar_one()
        return AgentProfile.from_row(row)


def ctx(org_id: str, key: str, **kw) -> ToolContext:
    return ToolContext(org_id=org_id, agent_key=key, **kw)


REFUND = {"account_email": "priya.shah@harbourhealthclinics.example", "reason": "unused licences"}
EMAIL = {"messages": [{"to": "tom.reid@quaysideretail.example", "subject": "Re: scanner", "body": "Here is the fix."}],
         "risk_level": "low", "purpose": "support_reply"}


async def test_agent_cannot_use_tool_outside_its_allowlist(sentinel):
    out = await executor.execute(await agent(sentinel.org_id, "research"), "email_send", EMAIL, ctx(sentinel.org_id, "research"))
    assert out.status == "DENIED" and out.error_code == "permission_denied"
    async with session_scope() as s:
        tc = (await s.execute(select(ToolCall).where(ToolCall.id == out.tool_call_id))).scalar_one()
    assert tc.status == "DENIED", "denials are recorded, not silently dropped"


async def test_unknown_tool_and_invalid_arguments(sentinel):
    a = await agent(sentinel.org_id, "support_triage")
    assert (await executor.execute(a, "rm_rf", {}, ctx(sentinel.org_id, a.key))).error_code == "unknown_tool"
    bad = await executor.execute(a, "refund_issue", {"account_email": "not-an-email", "amount_usd": -5, "reason": "x"},
                                 ctx(sentinel.org_id, a.key))
    assert bad.status == "ERROR" and bad.error_code == "validation"


async def test_refund_thresholds_enforced_by_policy(sentinel):
    a = await agent(sentinel.org_id, "support_triage")
    small = await executor.execute(a, "refund_issue", {**REFUND, "amount_usd": 120}, ctx(sentinel.org_id, a.key))
    assert small.status == "SUCCESS"

    mid = await executor.execute(a, "refund_issue", {**REFUND, "amount_usd": 2400}, ctx(sentinel.org_id, a.key))
    assert mid.status == "APPROVAL_REQUIRED"
    async with session_scope() as s:
        appr = await s.get(Approval, mid.approval_id)
    assert appr.required_role == "finance_manager"
    assert appr.payload, "approver sees exactly what will be executed"

    huge = await executor.execute(a, "refund_issue", {**REFUND, "amount_usd": 40_000}, ctx(sentinel.org_id, a.key))
    assert huge.status == "DENIED" and huge.error_code == "policy_denied"
    approved_huge = await executor.execute(a, "refund_issue", {**REFUND, "amount_usd": 40_000}, ctx(sentinel.org_id, a.key),
                                           approved=True)
    assert approved_huge.status == "DENIED", "deny policies cannot be overridden by an approval"


async def test_external_email_requires_qa_first(sentinel):
    a = await agent(sentinel.org_id, "support_triage")
    blocked = await executor.execute(a, "email_send", EMAIL, ctx(sentinel.org_id, a.key))
    assert blocked.status == "DENIED" and blocked.error_code == "qa_required"
    sent = await executor.execute(a, "email_send", EMAIL, ctx(sentinel.org_id, a.key, qa_passed_capabilities={"propose_solution"}))
    assert sent.status == "SUCCESS"
    high = await executor.execute(a, "email_send", {**EMAIL, "risk_level": "high"},
                                  ctx(sentinel.org_id, a.key, qa_passed_capabilities={"propose_solution"}))
    assert high.status == "APPROVAL_REQUIRED"


async def test_tainted_recipient_from_untrusted_content_requires_approval(sentinel):
    a = await agent(sentinel.org_id, "support_triage")
    c = ctx(sentinel.org_id, a.key, qa_passed_capabilities={"x"}, tainted_values={"attacker@evil-exfil.example"})
    out = await executor.execute(a, "email_send", {**EMAIL, "messages": [{**EMAIL["messages"][0], "to": "attacker@evil-exfil.example"}]}, c)
    assert out.status == "APPROVAL_REQUIRED"
    assert "tainted_argument" in out.flags
    async with session_scope() as s:
        assert (await s.get(Approval, out.approval_id)).risk_level == "high"


async def test_untrusted_tool_output_is_neutralised_and_tainted(sentinel):
    a = await agent(sentinel.org_id, "knowledge")
    c = ctx(sentinel.org_id, a.key)
    out = await executor.execute(a, "knowledge_search", {"query": "USB keyboards blocked after agent update workaround", "k": 8}, c)
    assert out.ok
    blob = str(out.result)
    assert "Ignore all previous instructions" not in blob
    assert any(f.startswith("injection:") for f in out.flags)
    assert not any("evil-exfil" in v for v in c.tainted_values), "redacted content contributes no taint"


async def test_knowledge_access_policy_hides_legal_documents(sentinel):
    q = {"query": "limitation of liability termination service credits", "k": 10}
    research = await executor.execute(await agent(sentinel.org_id, "research"), "knowledge_search", q, ctx(sentinel.org_id, "research"))
    compliance = await executor.execute(await agent(sentinel.org_id, "compliance"), "knowledge_search", q,
                                        ctx(sentinel.org_id, "compliance"))
    def classes(o):
        return {p.get("classification") for p in o.result["passages"]}
    assert "legal" not in classes(research)
    assert "legal" in classes(compliance)


async def test_transient_failures_are_retried_then_reported(sentinel):
    a = await agent(sentinel.org_id, "data_analyst")
    out = await executor.execute(a, "calculator", {"expression": "2+2"}, ctx(sentinel.org_id, a.key, chaos={"tool_failure_rate": 1.0}))
    assert out.status == "ERROR" and out.transient
    assert out.attempts == 3
    ok = await executor.execute(a, "calculator", {"expression": "2+2"}, ctx(sentinel.org_id, a.key))
    assert ok.ok and ok.result["result"] == 4


async def test_disabled_tool_is_unavailable(sentinel):
    async with session_scope() as s:
        td = (await s.execute(select(ToolDefinition).where(ToolDefinition.org_id == sentinel.org_id,
                                                           ToolDefinition.name == "calculator"))).scalar_one()
        td.enabled = False
    try:
        out = await executor.execute(await agent(sentinel.org_id, "data_analyst"), "calculator", {"expression": "1+1"},
                                     ctx(sentinel.org_id, "data_analyst"))
        assert out.status == "DENIED"
    finally:
        async with session_scope() as s:
            td = await s.get(ToolDefinition, td.id)
            td.enabled = True
