"""Supervisor planning policy: objective → structured plan (task DAG)."""

from __future__ import annotations

import math
import re

DEFAULT_CRITERIA = {
    "security_need": 0.35, "company_size_fit": 0.20, "technology_fit": 0.20,
    "regulatory_pressure": 0.15, "footprint": 0.10,
}
DEFAULT_INDUSTRIES = [
    "Financial Services", "Healthcare", "Legal Services", "Manufacturing", "Logistics", "Retail",
    "Professional Services", "Pharmaceuticals", "Energy & Utilities", "Public Sector", "Education", "Technology",
]


def classify(text: str, params: dict) -> str:
    if params.get("template"):
        return params["template"]
    t = text.lower()
    if params.get("ticket") or "support ticket" in t or "customer ticket" in t:
        return "support_ticket"
    if re.search(r"\b(prospect|lead|b2b|potential customers|opportunit|outreach)\w*", t):
        return "b2b_sales"
    if re.search(r"\bcompetit\w*", t):
        return "competitive_research"
    if re.search(r"\b(contract|document|policy|policies|summari[sz]e)\w*", t):
        return "document_analysis"
    return "generic"


def _t(key, title, agent, capability, deps=(), inp=None, review=None, budget=2.0, priority=5, instance="", desc=""):
    d = {"key": key, "title": title, "agent": agent, "capability": capability, "depends_on": list(deps),
         "input": inp or {}, "budget_usd": budget, "priority": priority, "instance": instance, "description": desc}
    if review:
        d["requires_review"] = True
        d["review_criteria"] = review
    return d


def plan_b2b(text: str, p: dict) -> dict:
    target = int(p.get("target_count", 100))
    top_n = int(p.get("top_n", 20))
    n_agents = max(1, min(int(p.get("research_agents", 4)), 8))
    per_agent = math.ceil(target * 1.3 / n_agents)
    discover = [
        _t(f"discover.{i + 1}", f"Discover candidate companies — segment {i + 1}", "research", "discover_companies",
           ["strategy"], {"segment_index": i, "num_segments": n_agents, "quota": per_agent}, budget=1.5,
           instance=f"Research Agent #{i + 1}")
        for i in range(n_agents)
    ]
    shortlist = top_n + max(10, top_n // 2)
    tasks = [
        _t("strategy", "Create research strategy and segment the market", "supervisor", "research_strategy", budget=1.0, priority=9),
        *discover,
        _t("enrich", "Collect company intelligence for all candidates", "company_intel", "enrich_companies",
           [d["key"] for d in discover], budget=3.0),
        _t("score", "Score opportunities against business criteria", "data_analyst", "score_opportunities", ["enrich"],
           {"target_count": target}, review={"type": "scoring", "min_items": min(target, 50), "threshold": 0.75}, budget=2.0),
        _t("decision_makers", "Identify likely decision-maker roles for the shortlist", "research",
           "identify_decision_makers", ["score"], {"shortlist_size": shortlist}, budget=2.0, instance="Research Agent #1"),
        _t("outreach", "Write personalised outreach for shortlisted prospects", "outreach", "write_outreach",
           ["decision_makers", "score"], review={"type": "outreach", "threshold": 0.8, "min_items": top_n}, budget=3.0),
        _t("compliance", "Check outreach against communication rules", "compliance", "check_outreach", ["outreach"], budget=1.0),
        _t("rank", f"Rank candidates and select the best {top_n}", "supervisor", "rank_candidates",
           ["compliance", "score", "decision_makers", "outreach"], {"top_n": top_n}, budget=1.0, priority=8),
        _t("crm", f"Create CRM opportunities for the top {top_n}", "crm", "create_opportunities", ["rank"], budget=1.0),
        _t("send", "Send approved outreach", "outreach", "send_outreach", ["rank", "outreach"], budget=1.0),
        _t("report", "Compile final sales-intelligence deliverable", "supervisor", "synthesize_deliverable",
           ["rank", "crm", "send", "score"], {"deliverable": True}, budget=1.0, priority=8),
    ]
    return {
        "summary": (f"Find {target} qualified companies for '{p.get('product', 'the product')}' in "
                    f"{p.get('region', 'the United Kingdom')} ({p.get('min_employees', 200)}–{p.get('max_employees', 5000)} employees) "
                    f"using {n_agents} parallel research agents, enrich, score, prepare outreach for a shortlist of {shortlist}, "
                    f"QA + compliance check, and present the best {top_n} for human approval before CRM updates."),
        "strategy": {"template": "b2b_sales", "parallel_research_agents": n_agents, "per_agent_quota": per_agent,
                     "criteria": p.get("criteria") or DEFAULT_CRITERIA},
        "tasks": tasks,
    }


def plan_support(text: str, p: dict) -> dict:
    tasks = [
        _t("triage", "Triage ticket: category, urgency, sentiment", "support_triage", "triage_ticket", priority=9, budget=0.5),
        _t("account", "Look up customer account and entitlements", "crm", "lookup_account", ["triage"], budget=0.5),
        _t("knowledge", "Retrieve relevant documentation", "knowledge", "retrieve_docs", ["triage"], budget=0.5),
        _t("solution", "Propose technical solution with citations", "technical", "propose_solution",
           ["triage", "account", "knowledge"], review={"type": "support_answer", "threshold": 0.75}, budget=1.0),
        _t("risk", "Evaluate risk and decide on human review", "risk_evaluator", "evaluate_risk",
           ["triage", "account", "solution"], budget=0.5),
        _t("respond", "Respond to customer per policy", "support_triage", "respond_to_customer",
           ["triage", "account", "solution", "risk"], budget=0.5),
        _t("summary", "Summarise resolution and classify product issue", "document", "summarize_resolution",
           ["triage", "solution", "risk", "respond"], budget=0.5),
        _t("finalize", "Update ticket, knowledge insights and analytics", "operations", "finalize_ticket",
           ["triage", "summary", "risk", "respond"], {"deliverable": True}, budget=0.5),
    ]
    return {"summary": "Resolve the incoming support ticket: triage, account lookup, cited knowledge retrieval, "
                       "technical solution, QA, risk-based routing (auto-reply vs human approval), and close-out.",
            "strategy": {"template": "support_ticket"}, "tasks": tasks}


def plan_competitive(text: str, p: dict) -> dict:
    tasks = [
        _t("competitors", "Find competitors", "research", "find_competitors", inp={"query": p.get("market", text)}, instance="Research Agent #1"),
        _t("pricing", "Collect competitor pricing", "research", "collect_pricing", ["competitors"], instance="Research Agent #1"),
        _t("analysis", "Analyse competitive positioning", "data_analyst", "analyze_positioning", ["competitors", "pricing"],
           review={"type": "report", "threshold": 0.75}),
        _t("report", "Generate executive report", "document", "generate_report", ["analysis", "pricing", "competitors"],
           {"deliverable": True, "title": p.get("report_title", "Competitive Landscape — Executive Report")}),
    ]
    return {"summary": "Identify competitors, collect and normalise pricing (delegating gaps), analyse positioning, "
                       "validate claims with QA and produce an executive report.",
            "strategy": {"template": "competitive_research"}, "tasks": tasks}


def plan_document(text: str, p: dict) -> dict:
    tasks = [
        _t("retrieve", "Retrieve relevant document passages", "document", "summarize_documents", inp={"query": text}),
        _t("extract", "Extract structured obligations and facts", "document", "extract_structured", ["retrieve"],
           review={"type": "report", "threshold": 0.7}),
        _t("report", "Draft business summary", "document", "generate_report", ["extract", "retrieve"],
           {"deliverable": True, "title": p.get("report_title", "Document Analysis")}),
    ]
    return {"summary": "Retrieve, summarise and extract structured information from organisational documents with citations.",
            "strategy": {"template": "document_analysis"}, "tasks": tasks}


def plan_generic(text: str, p: dict) -> dict:
    tasks = [
        _t("research", "Research the objective", "research", "general_research", inp={"query": text}, instance="Research Agent #1"),
        _t("analysis", "Analyse findings", "data_analyst", "analyze_findings", ["research"]),
        _t("report", "Produce deliverable", "document", "generate_report", ["analysis", "research"],
           {"deliverable": True, "title": p.get("report_title", "Objective Report")}, review={"type": "report", "threshold": 0.7}),
    ]
    return {"summary": "Research the objective using knowledge base and web sources, analyse, and produce a cited report.",
            "strategy": {"template": "generic"}, "tasks": tasks}


PLANNERS = {
    "b2b_sales": plan_b2b, "support_ticket": plan_support, "competitive_research": plan_competitive,
    "document_analysis": plan_document, "generic": plan_generic,
}


def plan_objective(raw: dict) -> dict:
    obj = raw.get("objective", {})
    text, params = obj.get("text", ""), obj.get("parameters", {}) or {}
    template = classify(text, params)
    plan = PLANNERS.get(template, plan_generic)(text, params)
    available = {a["key"] for a in raw.get("agents", [])}
    if available:
        # degrade gracefully if an org disabled a specialist: route its work to the closest generalist
        fallback = {"company_intel": "research", "risk_evaluator": "compliance", "knowledge": "research",
                    "technical": "document", "support_triage": "operations"}
        for t in plan["tasks"]:
            if t["agent"] not in available and fallback.get(t["agent"]) in available:
                t["agent"] = fallback[t["agent"]]
    return plan
