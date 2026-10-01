"""Runtime + orchestrator behaviour driven by a scripted model provider."""

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.models import Approval, EventRecord, ExecutionStep, ModelUsage, Project, ToolCall
from app.services import approvals, orchestrator
from app.services.approvals import ApprovalError
from tests.conftest import wait_for_project
from tests.integration.helpers import delegate, final, make_project, task, tasks_of, tool, wait_task


def echo(raw):
    return final({"task": raw["task"]["key"], "deps": sorted(raw.get("dependencies", {}))})


async def test_sequential_and_parallel_dependencies(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_echo"] = echo
    pid, ids = await make_project(sentinel.org_id, [
        {"key": "a", "agent": "data_analyst", "capability": "t_echo"},
        {"key": "b", "agent": "data_analyst", "capability": "t_echo"},
        {"key": "c", "agent": "data_analyst", "capability": "t_echo", "depends_on": ["a", "b"]},
    ])
    p = await wait_for_project(pid)
    assert p.status == "COMPLETED"
    a, b, c = [await task(ids[k]) for k in "abc"]
    assert c.output["deps"] == ["a", "b"], "downstream task receives both upstream results"
    assert c.started_at >= max(a.completed_at, b.completed_at)


async def test_delegation_creates_child_tasks_and_resumes_parent(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_parent"] = lambda raw: (
        final({"children": [c["output_summary"] for c in raw["children"]]}) if raw.get("children") else
        delegate({"title": "Compute part one", "agent_key": "data_analyst", "capability": "t_echo", "input": {"part": 1}},
                 {"title": "Compute part two", "agent_key": "data_analyst", "capability": "t_echo", "input": {"part": 2}}))
    primary.scripts["t_echo"] = echo
    pid, ids = await make_project(sentinel.org_id, [{"key": "parent", "agent": "research", "capability": "t_parent"}])
    assert (await wait_for_project(pid)).status == "COMPLETED"
    children = [t for t in await tasks_of(pid) if t.parent_task_id == ids["parent"]]
    assert len(children) == 2
    assert all(c.delegation_depth == 1 and c.status == "COMPLETED" for c in children)
    assert len((await task(ids["parent"])).output["children"]) == 2


def _denied_delegation(raw) -> str | None:
    for o in raw.get("observations", []):
        if o.get("tool") == "delegate" and o.get("status") == "DENIED":
            return o["error"]
    return None


async def test_delegation_to_unpermitted_agent_is_rejected(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_bad_delegate"] = lambda raw: (
        final({"rejected": _denied_delegation(raw)}) if _denied_delegation(raw) else
        delegate({"title": "Send emails to everyone", "agent_key": "outreach", "capability": "t_echo"}))
    pid, ids = await make_project(sentinel.org_id, [{"key": "p", "agent": "research", "capability": "t_bad_delegate"}])
    await wait_for_project(pid)
    t = await task(ids["p"])
    assert "may not delegate to outreach" in t.output["rejected"]
    assert not [x for x in await tasks_of(pid) if x.parent_task_id]


async def test_delegation_cycle_is_detected_through_the_ancestor_chain(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_root"] = lambda raw: final({"ok": True}) if raw.get("children") else delegate(
        {"title": "Investigate topic", "agent_key": "research", "capability": "t_cycle", "input": {"topic": "x"}})
    primary.scripts["t_cycle"] = lambda raw: (
        final({"cycle_blocked": _denied_delegation(raw)}) if _denied_delegation(raw) else
        delegate({"title": "Investigate topic", "agent_key": "research", "capability": "t_cycle", "input": {"topic": "x"}}))
    pid, ids = await make_project(sentinel.org_id, [{"key": "root", "agent": "research", "capability": "t_root"}])
    assert (await wait_for_project(pid)).status == "COMPLETED"
    children = [t for t in await tasks_of(pid) if t.parent_task_id]
    assert len(children) == 1, "the repeated delegation must not create a grandchild"
    assert "delegation cycle" in children[0].output["cycle_blocked"]


async def test_max_delegation_depth_enforced(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_deep"] = lambda raw: (
        final({"blocked": _denied_delegation(raw)}) if _denied_delegation(raw) else
        delegate({"title": "Go deeper", "agent_key": "data_analyst", "capability": "t_echo"}))
    pid, ids = await make_project(sentinel.org_id, [{"key": "deep", "agent": "research", "capability": "t_deep", "delegation_depth": 3}])
    await wait_for_project(pid)
    assert "max delegation depth" in (await task(ids["deep"])).output["blocked"]


async def test_fan_out_limit_enforced(sentinel, scripted, pool):
    primary, *_ = scripted
    subs = [{"title": f"Shard number {i}", "agent_key": "data_analyst", "capability": "t_echo", "input": {"i": i}} for i in range(9)]
    primary.scripts["t_fan"] = lambda raw: final({"blocked": _denied_delegation(raw)}) if _denied_delegation(raw) else delegate(*subs)
    pid, ids = await make_project(sentinel.org_id, [{"key": "fan", "agent": "research", "capability": "t_fan"}])
    await wait_for_project(pid)
    assert "fan-out limit" in (await task(ids["fan"])).output["blocked"]


async def test_tool_loop_is_detected_and_escalated(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_loop"] = lambda raw: tool("calculator", {"expression": "1+1"})
    pid, ids = await make_project(sentinel.org_id, [{"key": "loop", "agent": "data_analyst", "capability": "t_loop"}])
    t = await wait_task(ids["loop"], {"ESCALATED"})
    assert "loop" in t.error
    async with session_scope() as s:
        appr = (await s.execute(select(Approval).where(Approval.task_id == t.id))).scalar_one()
        calls = (await s.execute(select(ToolCall).where(ToolCall.task_id == t.id))).scalars().all()
    assert appr.action_type == "escalation"
    assert len(calls) == 2, "the third identical call is stopped before execution"
    await orchestrator.cancel_project(pid, "test")


async def test_malformed_model_output_is_repaired(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_malformed"] = lambda raw: final({"repaired": True}) if raw.get("previous_output_error") else "Sure! {not json"
    pid, ids = await make_project(sentinel.org_id, [{"key": "m", "agent": "data_analyst", "capability": "t_malformed"}])
    assert (await wait_for_project(pid)).status == "COMPLETED"
    async with session_scope() as s:
        steps = (await s.execute(select(ExecutionStep).where(ExecutionStep.task_id == ids["m"]))).scalars().all()
    assert any(st.action == "malformed_output" for st in steps)
    assert (await task(ids["m"])).output == {"repaired": True}


async def test_provider_outage_falls_back_to_secondary(sentinel, scripted, pool):
    primary, backup, _ = scripted
    primary.fail = True
    backup.scripts["t_echo"] = echo
    pid, ids = await make_project(sentinel.org_id, [{"key": "f", "agent": "data_analyst", "capability": "t_echo"}])
    assert (await wait_for_project(pid)).status == "COMPLETED"
    async with session_scope() as s:
        usage = (await s.execute(select(ModelUsage).where(ModelUsage.task_id == ids["f"]))).scalars().all()
        events = (await s.execute(select(EventRecord.type).where(EventRecord.task_id == ids["f"]))).scalars().all()
    assert any(not u.success and u.provider == "local" for u in usage)
    assert any(u.success and u.provider == "local-backup" and u.fallback_from for u in usage)
    assert "MODEL_FALLBACK" in events


async def test_transient_failure_is_retried_with_backoff(sentinel, scripted, pool):
    primary, backup, _ = scripted
    primary.fail = backup.fail = True
    primary.scripts["t_echo"] = echo
    pid, ids = await make_project(sentinel.org_id, [{"key": "r", "agent": "data_analyst", "capability": "t_echo"}])
    t = await wait_task(ids["r"], {"RETRY_SCHEDULED"})
    assert t.retries == 1 and t.next_run_at is not None
    primary.fail = backup.fail = False
    assert (await wait_for_project(pid)).status == "COMPLETED"
    t = await task(ids["r"])
    assert t.attempt == 2 and t.status == "COMPLETED"


def _refund_script(raw):
    obs = raw.get("observations", [])
    if not obs:
        return tool("refund_issue", {"account_email": "priya.shah@harbourhealthclinics.example", "amount_usd": 2400,
                                     "reason": "unused licences"})
    return final({"status": obs[-1]["status"], "error": obs[-1].get("error")})


async def test_approval_rbac_edit_and_resume(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_refund"] = _refund_script
    pid, ids = await make_project(sentinel.org_id, [{"key": "refund", "agent": "support_triage", "capability": "t_refund"}])
    await wait_task(ids["refund"], {"WAITING_APPROVAL"})
    async with session_scope() as s:
        appr = (await s.execute(select(Approval).where(Approval.task_id == ids["refund"]))).scalar_one()
        with pytest.raises(ApprovalError) as e:
            await approvals.decide(s, appr, sentinel.principal("approver@sentinel.example"), "approve")
        assert e.value.status_code == 403, "a generic approver cannot release a finance-gated refund"
        with pytest.raises(ApprovalError):
            await approvals.decide(s, appr, sentinel.principal("finance@sentinel.example"), "edit",
                                   {"account_email": "x", "amount_usd": -1, "reason": "x"})
        await approvals.decide(s, appr, sentinel.principal("finance@sentinel.example"), "edit",
                               {"account_email": "priya.shah@harbourhealthclinics.example", "amount_usd": 1800, "reason": "partial"})
    await orchestrator.apply_approval(appr.id)
    assert (await wait_for_project(pid)).status == "COMPLETED"
    async with session_scope() as s:
        executed = (await s.execute(select(ToolCall).where(ToolCall.task_id == ids["refund"], ToolCall.status == "SUCCESS"))).scalar_one()
    assert executed.arguments["amount_usd"] == 1800, "the edited payload, not the original, is executed"


async def test_rejected_approval_is_observed_by_the_agent(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_refund"] = _refund_script
    pid, ids = await make_project(sentinel.org_id, [{"key": "refund", "agent": "support_triage", "capability": "t_refund"}])
    await wait_for_project(pid, approve_as=sentinel.principal("finance@sentinel.example"),
                           decide=lambda a: ("reject", None, "not eligible under policy"))
    out = (await task(ids["refund"])).output
    assert out["status"] == "REJECTED" and "not eligible" in out["error"]
    async with session_scope() as s:
        assert not (await s.execute(select(ToolCall).where(ToolCall.task_id == ids["refund"], ToolCall.status == "SUCCESS"))).first()


async def test_budget_exhaustion_pauses_for_extension(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_budget"] = lambda raw: final({"ok": True}) if raw.get("observations") else tool("calculator", {"expression": "6*7"})
    pid, ids = await make_project(sentinel.org_id, [{"key": "b", "agent": "data_analyst", "capability": "t_budget", "budget_usd": 0.00001}])
    await wait_task(ids["b"], {"WAITING_APPROVAL"})
    async with session_scope() as s:
        appr = (await s.execute(select(Approval).where(Approval.task_id == ids["b"]))).scalar_one()
    assert appr.action_type == "budget_extension" and appr.payload["scope"] == "task"
    await wait_for_project(pid, approve_as=sentinel.principal("admin@sentinel.example"))
    t = await task(ids["b"])
    assert t.status == "COMPLETED" and t.budget_usd > 1


async def test_worker_crash_is_recovered_from_checkpoint(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_echo"] = echo
    pid, ids = await make_project(sentinel.org_id, [{"key": "c", "agent": "data_analyst", "capability": "t_echo"}],
                                  chaos={"worker_crash_rate": 1.0})
    assert (await wait_for_project(pid)).status == "COMPLETED"
    async with session_scope() as s:
        msgs = (await s.execute(select(EventRecord.message).where(EventRecord.task_id == ids["c"]))).scalars().all()
    assert any("Simulated worker crash" in m for m in msgs)
    assert any("lease expired" in m for m in msgs)


async def test_cancel_project_cancels_tasks_and_expires_approvals(sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_refund"] = _refund_script
    pid, ids = await make_project(sentinel.org_id, [{"key": "refund", "agent": "support_triage", "capability": "t_refund"}])
    await wait_task(ids["refund"], {"WAITING_APPROVAL"})
    await orchestrator.cancel_project(pid, "test")
    async with session_scope() as s:
        assert (await s.get(Project, pid)).status == "CANCELLED"
        appr = (await s.execute(select(Approval).where(Approval.task_id == ids["refund"]))).scalar_one()
    assert appr.status == "EXPIRED"
    assert (await task(ids["refund"])).status == "CANCELLED"
