import pytest

from app.seed.agents import AGENTS
from app.services.model_gateway.local_engine.planning import classify, plan_objective
from app.services.planning.dag import PlanValidationError, levels, validate_plan
from app.services.projects import TEMPLATES

AGENT_KEYS = {a["key"] for a in AGENTS}


def _plan(template: str, **params):
    t = TEMPLATES[template]
    raw = {"mode": "planning", "objective": {"text": t["objective"], "parameters": {**t["parameters"], **params}},
           "agents": [{"key": k} for k in AGENT_KEYS]}
    return plan_objective(raw)


@pytest.mark.parametrize(("text", "params", "expected"), [
    ("Find 50 qualified prospects and draft outreach", {}, "b2b_sales"),
    ("Research competitors and their pricing", {}, "competitive_research"),
    ("Summarise our contract obligations", {}, "document_analysis"),
    ("Resolve this", {"ticket": {"subject": "x"}}, "support_ticket"),
    ("Plan the team offsite", {}, "generic"),
])
def test_objective_classification(text, params, expected):
    assert classify(text, params) == expected


def test_b2b_plan_is_a_valid_dag_with_parallel_research():
    spec = validate_plan(_plan("b2b_sales"), AGENT_KEYS, 200)
    lv = levels(spec.tasks)
    discover = [t.key for t in spec.tasks if t.key.startswith("discover")]
    assert len(discover) == 4
    assert len({lv[k] for k in discover}) == 1, "discovery shards must run in parallel"
    keys = [t.key for t in spec.tasks]
    for before, after in [("enrich", "score"), ("score", "decision_makers"), ("outreach", "compliance"), ("crm", "report")]:
        assert lv[keys[keys.index(before)]] < lv[keys[keys.index(after)]]
    reviewed = {t.key for t in spec.tasks if t.requires_review}
    assert {"score", "outreach"} <= reviewed
    assert sum(bool(t.input.get("deliverable")) for t in spec.tasks) == 1


def test_research_parallelism_follows_parameters():
    spec = validate_plan(_plan("b2b_sales", research_agents=6), AGENT_KEYS, 200)
    assert sum(t.key.startswith("discover") for t in spec.tasks) == 6


@pytest.mark.parametrize("template", ["competitive_research", "document_analysis", "generic"])
def test_other_templates_validate(template):
    raw = _plan(template)
    if template == "generic":
        raw["tasks"][0]["title"] = raw["tasks"][0]["title"] or "Do the thing"
    validate_plan(raw, AGENT_KEYS, 200)


def test_planner_reroutes_disabled_specialists():
    t = TEMPLATES["b2b_sales"]
    available = AGENT_KEYS - {"company_intel"}
    raw = plan_objective({"objective": {"text": t["objective"], "parameters": t["parameters"]},
                          "agents": [{"key": k} for k in available]})
    assert all(task["agent"] != "company_intel" for task in raw["tasks"])
    validate_plan(raw, available, 200)


def _mini(tasks):
    return {"summary": "test plan", "tasks": tasks}


def _t(key, deps=(), agent="research"):
    return {"key": key, "title": f"Task {key}", "agent": agent, "depends_on": list(deps)}


def test_cycle_detected():
    with pytest.raises(PlanValidationError, match="cycle"):
        validate_plan(_mini([_t("a", ["c"]), _t("b", ["a"]), _t("c", ["b"])]), AGENT_KEYS, 10)


def test_unknown_dependency_and_agent_and_limits():
    with pytest.raises(PlanValidationError, match="unknown tasks"):
        validate_plan(_mini([_t("a", ["ghost"])]), AGENT_KEYS, 10)
    with pytest.raises(PlanValidationError, match="unknown or disabled agent"):
        validate_plan(_mini([_t("a", agent="hacker")]), AGENT_KEYS, 10)
    with pytest.raises(PlanValidationError, match="limit"):
        validate_plan(_mini([_t(f"t{i}") for i in range(5)]), AGENT_KEYS, 3)
    with pytest.raises(PlanValidationError, match="duplicate"):
        validate_plan(_mini([_t("a"), _t("a")]), AGENT_KEYS, 10)
    with pytest.raises(PlanValidationError, match="itself"):
        validate_plan(_mini([_t("a", ["a"])]), AGENT_KEYS, 10)
