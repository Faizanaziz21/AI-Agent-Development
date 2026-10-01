import pytest

from app.seed.agents import POLICIES
from app.services.policy.engine import decide, evaluate_condition, validate_condition

CTX = {"tool": "refund_issue", "agent_key": "support_triage", "facts": {"amount_usd": 2400, "recipient_count": 3},
       "args": {"messages": [{"to": "a@x.example"}]}, "project_template": "support_ticket"}


@pytest.mark.parametrize(("cond", "expected"), [
    ({}, True),
    ({"field": "tool", "op": "eq", "value": "refund_issue"}, True),
    ({"field": "tool", "op": "ne", "value": "refund_issue"}, False),
    ({"field": "facts.amount_usd", "op": "gt", "value": 1000}, True),
    ({"field": "facts.amount_usd", "op": "lte", "value": 1000}, False),
    ({"field": "agent_key", "op": "in", "value": ["support_triage", "crm"]}, True),
    ({"field": "agent_key", "op": "not_in", "value": ["support_triage"]}, False),
    ({"field": "facts.missing", "op": "exists"}, False),
    ({"field": "tool", "op": "matches", "value": "^refund_"}, True),
    ({"all": [{"field": "tool", "value": "refund_issue"}, {"field": "facts.amount_usd", "op": "gt", "value": 2000}]}, True),
    ({"any": [{"field": "tool", "value": "email_send"}, {"field": "tool", "value": "slack_message"}]}, False),
    ({"not": {"field": "tool", "value": "email_send"}}, True),
])
def test_condition_operators(cond, expected):
    assert evaluate_condition(cond, CTX) is expected


@pytest.mark.parametrize("bad", [
    {"all": "not-a-list"},
    {"op": "eq", "value": 1},
    {"field": "x", "op": "regex_magic", "value": 1},
    "string",
])
def test_invalid_conditions_rejected(bad):
    with pytest.raises(ValueError):
        validate_condition(bad)


def _p(name, effect, cond=None, role="approver", priority=100, scope="tool_call"):
    return {"name": name, "description": name, "effect": effect, "condition": cond or {}, "required_role": role,
            "priority": priority, "enabled": True, "scope": scope}


def test_effect_precedence_deny_wins():
    pols = [_p("needs approval", "require_approval"), _p("hard stop", "deny"), _p("qa", "require_qa")]
    d = decide(pols, "tool_call", CTX)
    assert d.effect == "deny"
    assert {m["name"] for m in d.matched} == {"needs approval", "hard stop", "qa"}


def test_require_qa_lifted_after_qa_passes():
    pols = [_p("qa first", "require_qa")]
    assert decide(pols, "tool_call", {**CTX, "qa_passed": False}).effect == "require_qa"
    assert decide(pols, "tool_call", {**CTX, "qa_passed": True}).effect == "allow"


def test_most_specific_role_is_required():
    pols = [_p("generic", "require_approval"), _p("finance", "require_approval", role="finance_manager")]
    assert decide(pols, "tool_call", CTX).required_role == "finance_manager"


def test_disabled_and_out_of_scope_policies_ignored():
    pols = [{**_p("off", "deny"), "enabled": False}, _p("kb", "deny", scope="knowledge_access")]
    assert decide(pols, "tool_call", CTX).effect == "allow"


def _seeded():
    return [{"scope": "tool_call", "required_role": "approver", "enabled": True, **p} for p in POLICIES]


def test_seeded_refund_thresholds():
    pols = _seeded()
    small = decide(pols, "tool_call", {**CTX, "facts": {"amount_usd": 300}})
    assert small.effect == "allow"
    mid = decide(pols, "tool_call", {**CTX, "facts": {"amount_usd": 2400}})
    assert (mid.effect, mid.required_role) == ("require_approval", "finance_manager")
    huge = decide(pols, "tool_call", {**CTX, "facts": {"amount_usd": 30_000}})
    assert huge.effect == "deny"


def test_seeded_legal_documents_restricted_to_compliance():
    pols = _seeded()
    assert decide(pols, "knowledge_access", {"agent_key": "research", "classification": "legal"}).effect == "deny"
    assert decide(pols, "knowledge_access", {"agent_key": "compliance", "classification": "legal"}).effect == "allow"
    assert decide(pols, "knowledge_access", {"agent_key": "research", "classification": "internal"}).effect == "allow"
