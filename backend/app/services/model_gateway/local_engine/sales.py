"""Skill policies for the Autonomous B2B Sales Intelligence Team."""

from __future__ import annotations

from app.services.model_gateway.local_engine.common import Ctx, call, chunks, delegate, final
from app.services.model_gateway.local_engine.planning import DEFAULT_CRITERIA, DEFAULT_INDUSTRIES

SIGNATURE = "\n\nBest regards,\nAlex Morgan\nEnterprise Account Executive, Sentinel Devices Ltd"
OPT_OUT = "\n\nIf this isn't relevant, reply 'stop' or use the unsubscribe link and we won't contact you again."


def _bounds(p: dict) -> tuple[int, int, str]:
    return int(p.get("min_employees", 200)), int(p.get("max_employees", 5000)), p.get("country", "United Kingdom")


# ---------------------------------------------------------------- supervisor: strategy
def research_strategy(c: Ctx) -> dict:
    p = c.params
    if "knowledge_search" in c.tools and not c.attempted("knowledge_search"):
        return call("knowledge_search", {"query": f"ideal customer profile target industries {p.get('product', '')}", "k": 4},
                    "Ground the strategy in the organisation's own sales material before segmenting the market.")
    icp = c.last("knowledge_search") or {}
    industries = p.get("industries") or DEFAULT_INDUSTRIES
    n = max(1, int(p.get("research_agents", 4)))
    lo, hi, country = _bounds(p)
    segments = []
    for i in range(n):
        inds = industries[i::n]
        segments.append({"index": i, "industries": inds,
                         "query": f"{' '.join(inds)} organisation {country} employees security"})
    return final(
        {"segments": segments, "country": country, "min_employees": lo, "max_employees": hi,
         "product": p.get("product", ""), "criteria": p.get("criteria") or DEFAULT_CRITERIA,
         "icp_evidence": [{"id": x.get("id"), "source": x.get("source"), "section": x.get("section")}
                          for x in icp.get("passages", [])]},
        f"Split {len(industries)} target industries into {n} parallel research segments for {country} "
        f"companies with {lo}–{hi} employees.",
        "Segmented by industry so research agents can work in parallel without overlap.",
    )


# ---------------------------------------------------------------- research: discovery
def discover_companies(c: Ctx) -> dict:
    strat = c.dep_output("strategy")
    idx = int(c.input.get("segment_index", 0))
    seg = (strat.get("segments") or [{}])[idx] if strat.get("segments") else {"industries": [], "query": "companies"}
    quota = int(c.input.get("quota", 30))
    searches = c.results("web_search")
    found: dict[str, dict] = {}
    for r in searches:
        for item in r.get("results", []):
            if item.get("type") == "company" and item.get("domain"):
                found.setdefault(item["domain"], item)
    page = 25
    if len(found) < quota and len(searches) < 3 and (not searches or searches[-1].get("count", 0) >= page):
        return call("web_search", {
            "query": seg.get("query", "companies"), "industries": seg.get("industries", []),
            "country": strat.get("country", "United Kingdom"), "min_employees": strat.get("min_employees"),
            "max_employees": strat.get("max_employees"), "result_type": "company", "limit": page,
            "offset": page * len(searches)},
            f"Searching segment {idx + 1} ({', '.join(seg.get('industries', [])[:3])}); {len(found)}/{quota} found so far.",
            next_action="continue paging until quota met")
    companies = [{"name": v["title"], "domain": v["domain"], "industry": v.get("industry"), "region": v.get("region"),
                  "source_url": v.get("url"), "snippet": v.get("snippet", "")[:200]} for v in list(found.values())[:quota]]
    return final({"segment_index": idx, "companies": companies, "count": len(companies),
                  "queries": len(searches)}, f"{len(companies)} companies found",
                 f"Collected {len(companies)} candidates for segment {idx + 1} from {len(searches)} searches.")


def verify_firmographics(c: Ctx) -> dict:
    domains = c.input.get("domains", [])
    done = {r.get("record", {}).get("domain") for r in c.results("company_registry_lookup") if r.get("record")}
    tried = {o["arguments"].get("domain") for o in c.attempted("company_registry_lookup")}
    for d in domains:
        if d not in tried:
            return call("company_registry_lookup", {"domain": d}, f"Verifying registry headcount for {d}.")
    verified = [{"domain": r["record"]["domain"], "employees": r["record"].get("employees"),
                 "source_url": r["record"].get("source_url"), "confidence": 0.95}
                for r in c.results("company_registry_lookup") if r.get("record")]
    return final({"verified": verified, "unresolved": sorted(set(domains) - done)},
                 f"Verified {len(verified)} of {len(domains)} records via the company registry")


# ---------------------------------------------------------------- company intelligence
def enrich_companies(c: Ctx) -> dict:
    cands: dict[str, dict] = {}
    for out in c.dep_outputs("discover_companies"):
        for co in out.get("companies", []):
            cands.setdefault(co["domain"], co)
    domains = sorted(cands)
    looked = {co["domain"]: co for r in c.results("company_lookup") for co in r.get("companies", []) if co.get("found")}
    tried = {d for o in c.attempted("company_lookup") if o.get("status") == "SUCCESS" for d in o["arguments"].get("domains", [])}
    pending = [d for d in domains if d not in tried]
    if pending:
        batch = pending[:40]
        return call("company_lookup", {"domains": batch},
                    f"Enriching {len(batch)} companies ({len(tried)}/{len(domains)} done).", next_action="continue enrichment")
    missing = [d for d, co in looked.items() if co.get("employees") is None]
    if missing and not c.children:
        groups = chunks(missing, max(1, -(-len(missing) // 4)))[:4]
        return delegate([
            {"title": f"Verify firmographics for {len(g)} companies (batch {i + 1})", "agent_key": "research",
             "capability": "verify_firmographics", "description": "Fill missing headcount from the official registry.",
             "input": {"domains": g}} for i, g in enumerate(groups)],
            f"{len(missing)} companies are missing headcount; delegating registry verification to research agents in parallel.")
    verified = {v["domain"]: v for ch in c.children for v in (ch.get("output") or {}).get("verified", [])}
    records = []
    for d, co in looked.items():
        prov = {"industry": {"source": "company_lookup", "confidence": 0.9},
                "employees": {"source": "company_lookup", "confidence": 0.85}}
        emp = co.get("employees")
        if emp is None and d in verified:
            emp = verified[d]["employees"]
            prov["employees"] = {"source": "company_registry_lookup", "confidence": 0.95, "url": verified[d]["source_url"]}
        records.append({
            "name": co["name"], "domain": d, "industry": co.get("industry"), "employees": emp,
            "country": co.get("country"), "region": co.get("region"), "hq_city": co.get("hq_city"),
            "uk_sites": co.get("uk_sites"), "revenue_band": co.get("revenue_band"),
            "tech_indicators": co.get("tech_indicators", []), "security_signals": co.get("security_signals", []),
            "source_url": co.get("source_url"), "provenance": prov,
        })
    if "entity_upsert" in c.tools and not c.attempted("entity_upsert"):
        return call("entity_upsert", {"entities": [
            {"type": "company", "name": r["name"], "source": r["source_url"] or "company_lookup", "confidence": 0.85,
             "attributes": {"industry": r["industry"], "employees": r["employees"], "region": r["region"],
                            "country": r["country"], "domain": r["domain"],
                            "technologies": [t["name"] for t in r["tech_indicators"] if t["confidence"] >= 0.7]}}
            for r in records[:200]]},
            "Persist enriched firmographics to structured memory with provenance.")
    return final({"companies": records, "count": len(records), "verified_by_registry": len(verified),
                  "still_missing": [r["domain"] for r in records if r["employees"] is None]},
                 f"Enriched {len(records)} companies; {len(verified)} headcounts verified via registry",
                 "Merged directory enrichment with delegated registry verification.")


# ---------------------------------------------------------------- data analyst: scoring
SCORING_CODE = '''
w = data["criteria"]
lo, hi, country, threshold = data["min"], data["max"], data["country"], data["threshold"]
TECH_FIT = {"Microsoft Intune": 1.0, "Jamf": 0.9, "Microsoft 365": 0.6, "Azure": 0.5, "CrowdStrike": 0.7,
            "SentinelOne": 0.7, "Okta": 0.5, "Zscaler": 0.6, "Citrix": 0.6}
REGULATED = {"Financial Services", "Healthcare", "Pharmaceuticals", "Public Sector", "Legal Services", "Energy & Utilities"}
scored, rejected = [], {}
for c in data["companies"]:
    emp = c.get("employees")
    reason = None
    if c.get("country") != country:
        reason = "outside target geography"
    elif emp is None:
        reason = "headcount unverified"
    elif emp < lo or emp > hi:
        reason = "outside employee range"
    if reason:
        rejected[reason] = rejected.get(reason, 0) + 1
        continue
    sig = c.get("security_signals", [])
    need = min(1.0, sum(s["weight"] * s["confidence"] for s in sig) / 18.0)
    mid = (lo * hi) ** 0.5
    size = max(0.0, 1 - abs(math.log(emp / mid)) / math.log(hi / mid))
    techs = [t for t in c.get("tech_indicators", []) if t["confidence"] >= 0.6]
    tech = min(1.0, sum(TECH_FIT.get(t["name"], 0.2) * t["confidence"] for t in techs) / 1.5) if techs else 0.1
    reg = 1.0 if c.get("industry") in REGULATED else 0.35
    foot = min(1.0, (c.get("uk_sites") or 1) / 6)
    parts = {"security_need": need, "company_size_fit": size, "technology_fit": tech, "regulatory_pressure": reg, "footprint": foot}
    total = sum(parts[k] * w.get(k, 0) for k in parts) / max(sum(w.values()), 1e-9) * 100
    scored.append({"name": c["name"], "domain": c["domain"], "industry": c.get("industry"), "employees": emp,
                   "region": c.get("region"), "uk_sites": c.get("uk_sites"), "score": round(total, 1),
                   "breakdown": {k: round(v, 3) for k, v in parts.items()},
                   "top_signals": sorted(sig, key=lambda s: -s["weight"] * s["confidence"])[:3],
                   "tech_indicators": c.get("tech_indicators", []), "provenance": c.get("provenance", {})})
scored.sort(key=lambda r: -r["score"])
qualified = [r for r in scored if r["score"] >= threshold]
result = {"qualified": qualified, "below_threshold": len(scored) - len(qualified), "rejected": rejected,
          "mean_score": round(statistics.fmean([r["score"] for r in scored]), 2) if scored else 0}
'''


def score_opportunities(c: Ctx) -> dict:
    p = c.params
    enrich = c.dep_output("enrich")
    lo, hi, country = _bounds(p)
    target = int(c.input.get("target_count", p.get("target_count", 100)))
    threshold = float(p.get("qualification_threshold", 40))
    criteria = p.get("criteria") or DEFAULT_CRITERIA
    if not c.attempted("python_sandbox"):
        return call("python_sandbox", {
            "code": "import math, statistics\n" + SCORING_CODE,
            "data": {"companies": enrich.get("companies", []), "criteria": criteria, "min": lo, "max": hi,
                     "country": country, "threshold": threshold}},
            "Compute weighted opportunity scores deterministically in the sandbox rather than estimating them.")
    res = c.last("python_sandbox")
    if not res:
        return final({"qualified": [], "error": "scoring sandbox failed"}, "Scoring failed", confidence=0.2)
    r = res.get("result") or {}
    qualified = r.get("qualified", [])[:target]
    return final({
        "qualified": qualified, "qualified_count": len(qualified), "target_count": target,
        "candidates_evaluated": len(enrich.get("companies", [])), "rejected": r.get("rejected", {}),
        "below_threshold": r.get("below_threshold", 0), "mean_score": r.get("mean_score"), "criteria": criteria,
        "threshold": threshold, "method": "python_sandbox weighted scoring"},
        f"{len(qualified)} qualified companies scored (target {target}); rejected: "
        + ", ".join(f"{k}: {v}" for k, v in r.get("rejected", {}).items()),
        confidence=0.9 if len(qualified) >= target else 0.7)


# ---------------------------------------------------------------- research: decision makers
def _roles_for(emp: int | None) -> list[str]:
    if (emp or 0) >= 1500:
        return ["Chief Information Security Officer", "Chief Information Officer", "Head of IT Infrastructure"]
    if (emp or 0) >= 500:
        return ["Head of Information Security", "IT Director", "Head of IT"]
    return ["IT Manager", "Operations Director", "Managing Director"]


def identify_decision_makers(c: Ctx) -> dict:
    score = c.dep_output("score")
    n = int(c.input.get("shortlist_size", 25))
    shortlist = score.get("qualified", [])[:n]
    if not c.attempted("people_search"):
        return call("people_search", {"domains": [s["domain"] for s in shortlist], "roles": ["security", "information", "it", "director", "chief"]},
                    f"Look up leadership for the {len(shortlist)} highest-scoring companies.")
    res = c.last("people_search") or {}
    people = {r["domain"]: r.get("people", []) for r in res.get("results", [])}
    prospects = []
    for s in shortlist:
        roles = _roles_for(s.get("employees"))
        contacts = people.get(s["domain"], [])
        ranked = sorted(contacts, key=lambda p: next((i for i, r in enumerate(roles) if r.lower() in p["title"].lower()), 9))
        primary = ranked[0] if ranked else None
        prospects.append({
            "name": s["name"], "domain": s["domain"], "score": s["score"], "target_roles": roles,
            "primary_contact": primary, "other_contacts": ranked[1:3],
            "role_rationale": (f"{s.get('employees')} employees → security ownership likely sits with "
                               f"{roles[0]}; budget holder {roles[1]}."),
        })
    with_contact = sum(1 for p in prospects if p["primary_contact"])
    return final({"prospects": prospects, "with_contact": with_contact},
                 f"Identified decision makers for {with_contact}/{len(prospects)} shortlisted companies")


# ---------------------------------------------------------------- outreach
def _claims(s: dict, strict: bool) -> list[dict]:
    claims = []
    sigs = s.get("top_signals") or []
    sig = next((x for x in sigs if x["confidence"] >= 0.7), None) if strict else (sigs[0] if sigs else None)
    if sig:
        claims.append({"text": f"{s['industry']} organisations like {s['name']} that are {sig['signal'].lower()}",
                       "evidence": f"security_signals:{sig['signal']}", "confidence": sig["confidence"]})
    techs = sorted(s.get("tech_indicators", []), key=lambda t: -t["confidence"]) if strict else s.get("tech_indicators", [])
    tech = next((t for t in techs if t["confidence"] >= 0.7), None) if strict else (techs[0] if techs else None)
    if tech:
        claims.append({"text": f"your {tech['name']} environment", "evidence": f"tech_indicators:{tech['name']} ({tech['source']})",
                       "confidence": tech["confidence"]})
    if s.get("employees"):
        conf = (s.get("provenance", {}).get("employees") or {}).get("confidence", 0.85)
        sites = s.get("uk_sites") or 1
        claims.append({"text": f"around {s['employees']:,} people across {sites} UK site{'s' if sites > 1 else ''}",
                       "evidence": "employees", "confidence": conf})
    return claims


def write_outreach(c: Ctx) -> dict:
    p = c.params
    dm = c.dep_output("decision_makers")
    scores = {s["domain"]: s for s in c.dep_output("score").get("qualified", [])}
    revision = int(c.task.get("revision", 0))
    flagged = {i.get("prospect_ref") for i in c.feedback_issues()}
    strict = revision > 0
    product = p.get("product", "Enterprise Device Control Platform")
    messages = []
    for pr in dm.get("prospects", []):
        contact = pr.get("primary_contact")
        s = scores.get(pr["domain"])
        if not contact or not s:
            continue
        claims = _claims(s, strict or pr["domain"] in flagged)
        first = contact["name"].split()[0]
        lines = [f"Hi {first},", ""]
        if claims:
            lines.append(f"I'm reaching out because {claims[0]['text']} are under growing pressure to control "
                         "how data leaves through USB drives, phones and other peripherals.")
        for cl in claims[1:]:
            lines.append(f"Given {cl['text']}, {product} can enforce device policies centrally without slowing teams down.")
        incident = any("incident" in (x.get("signal", "").lower()) for x in s.get("top_signals", []))
        if incident:
            lines.append("After a disclosed incident, we guarantee no sensitive file leaves via removable media again.")
        lines.append(f"Would a 20-minute walkthrough for your {contact['title']} priorities be useful next week?")
        body = "\n".join(lines) + SIGNATURE + OPT_OUT
        messages.append({"to": contact["email"], "subject": f"Device control for {s['name']}"[:90], "body": body,
                         "prospect_ref": pr["domain"], "company": s["name"], "industry": s["industry"],
                         "contact": contact, "claims": claims})
    if not c.attempted("email_draft"):
        return call("email_draft", {"messages": [{k: m[k] for k in ("to", "subject", "body", "prospect_ref")} for m in messages]},
                    f"Save {len(messages)} personalised drafts (revision {revision}) for QA review — nothing is sent.")
    drafts = c.last("email_draft") or {}
    return final({"messages": messages, "count": len(messages), "revision": revision,
                  "draft_ids": drafts.get("draft_ids", []),
                  "personalisation": "claims restricted to evidence with confidence ≥ 0.7" if strict else "first-pass personalisation"},
                 f"Drafted {len(messages)} personalised emails (revision {revision})",
                 "Revised to only use well-evidenced claims per QA feedback." if strict else "Drafted outreach from enriched firmographics.")


def send_outreach(c: Ctx) -> dict:
    rank = c.dep_output("rank")
    top = rank.get("top", [])
    msgs = [{"to": t["contact_email"], "subject": t["subject"], "body": t["body"], "prospect_ref": t["domain"]}
            for t in top if t.get("contact_email")]
    rej = c.human_decision("email_send")
    if rej:
        return final({"sent": 0, "status": "not_sent", "human_feedback": rej.get("error")},
                     "Outreach not sent — human reviewer declined", confidence=0.9)
    if not c.results("email_send"):
        return call("email_send", {"messages": msgs, "risk_level": "medium", "purpose": "outreach"},
                    f"Send {len(msgs)} QA- and compliance-approved emails; policy requires human approval for external outreach.")
    r = c.last("email_send")
    return final({"sent": r.get("sent", 0), "recipients": r.get("recipients", []), "status": "sent"},
                 f"Sent {r.get('sent', 0)} approved outreach emails")


# ---------------------------------------------------------------- compliance
def check_outreach(c: Ctx) -> dict:
    out = c.dep_output("outreach")
    msgs = out.get("messages", [])
    if not c.attempted("policy_check"):
        return call("policy_check", {"messages": [{k: m[k] for k in ("to", "subject", "body", "prospect_ref")} for m in msgs]},
                    "Check every draft against configurable communication rules (opt-out, identity, prohibited claims, recipients).")
    r = c.last("policy_check") or {}
    blocked = [x for x in r.get("results", []) if not x["passed"]]
    return final({"passed": [x["prospect_ref"] for x in r.get("results", []) if x["passed"]], "blocked": blocked,
                  "rules_applied": r.get("rules_applied", [])},
                 f"{r.get('passed', 0)} messages compliant, {len(blocked)} blocked",
                 "Blocked messages are excluded from final ranking." if blocked else "All messages compliant.")


# ---------------------------------------------------------------- supervisor: ranking + deliverable
DEFAULT_RANKING = {"opportunity_score": 0.6, "contact_seniority": 0.2, "evidence_strength": 0.2}


def rank_candidates(c: Ctx) -> dict:
    p = c.params
    top_n = int(c.input.get("top_n", p.get("top_n", 20)))
    weights = p.get("ranking_criteria") or DEFAULT_RANKING
    comp = c.dep_output("compliance")
    passed = set(comp.get("passed", []))
    scores = {s["domain"]: s for s in c.dep_output("score").get("qualified", [])}
    msgs = {m["prospect_ref"]: m for m in c.dep_output("outreach").get("messages", [])}
    price = float(p.get("price_per_endpoint_month_usd", 6.0))
    rows = []
    for dom, m in msgs.items():
        if dom not in passed or dom not in scores:
            continue
        s = scores[dom]
        title = m["contact"]["title"]
        seniority = 1.0 if title.startswith("Chief") else 0.75 if any(w in title for w in ("Head", "Director")) else 0.5
        evidence = sum(cl["confidence"] for cl in m["claims"]) / max(len(m["claims"]), 1)
        total = (weights.get("opportunity_score", 0) * s["score"] / 100 + weights.get("contact_seniority", 0) * seniority
                 + weights.get("evidence_strength", 0) * evidence) / max(sum(weights.values()), 1e-9) * 100
        rows.append({
            "domain": dom, "company": s["name"], "industry": s["industry"], "employees": s["employees"], "region": s["region"],
            "opportunity_score": s["score"], "rank_score": round(total, 1), "contact_name": m["contact"]["name"],
            "contact_title": title, "contact_email": m["to"], "subject": m["subject"], "body": m["body"],
            "estimated_annual_value_usd": round((s["employees"] or 0) * 0.85 * price * 12, -2),
            "rationale": f"Score {s['score']}, {title} contact, evidence strength {evidence:.2f}; "
                         + ", ".join(x["signal"] for x in s.get("top_signals", [])[:2]),
        })
    rows.sort(key=lambda r: -r["rank_score"])
    top = rows[:top_n]
    for i, r in enumerate(top, 1):
        r["rank"] = i
    return final({"top": top, "ranking_criteria": weights, "excluded_by_compliance": len(msgs) - len([d for d in msgs if d in passed]),
                  "pipeline_value_usd": sum(r["estimated_annual_value_usd"] for r in top)},
                 f"Selected top {len(top)} opportunities (pipeline ${sum(r['estimated_annual_value_usd'] for r in top):,.0f}/yr)",
                 "Ranked by explicit weighted criteria; compliance-blocked prospects excluded.", confidence=0.88)


def create_opportunities(c: Ctx) -> dict:
    top = c.dep_output("rank").get("top", [])
    rej = c.human_decision("crm_bulk_create_opportunities")
    if rej:
        return final({"created": 0, "status": "rejected", "human_feedback": rej.get("error")}, "CRM update declined by reviewer")
    if not c.results("crm_bulk_create_opportunities"):
        return call("crm_bulk_create_opportunities", {"records": [
            {"company_name": t["company"], "domain": t["domain"], "industry": t["industry"], "employees": t["employees"],
             "region": t["region"], "score": t["opportunity_score"], "amount_usd": t["estimated_annual_value_usd"],
             "contact_name": t["contact_name"], "contact_title": t["contact_title"], "contact_email": t["contact_email"],
             "notes": t["rationale"]} for t in top], "stage": "qualification"},
            f"Create {len(top)} opportunities; changing CRM opportunities requires human approval.")
    r = c.last("crm_bulk_create_opportunities") or {}
    return final(r, f"CRM updated: {r.get('opportunities_created', 0)} opportunities created, "
                    f"{len(r.get('duplicates_detected', []))} duplicates merged")


def synthesize_deliverable(c: Ctx) -> dict:
    rank, crm, send, score = c.dep_output("rank"), c.dep_output("crm"), c.dep_output("send"), c.dep_output("score")
    top = rank.get("top", [])
    if "file_write" in c.tools and not c.attempted("file_write"):
        lines = ["# Sales Intelligence — Top Opportunities", "",
                 f"Qualified companies: {score.get('qualified_count')} | Pipeline: ${rank.get('pipeline_value_usd', 0):,.0f}/yr", "",
                 "| # | Company | Industry | Employees | Score | Contact |", "|---|---|---|---|---|---|"]
        lines += [f"| {t['rank']} | {t['company']} | {t['industry']} | {t['employees']:,} | {t['rank_score']} | {t['contact_name']} ({t['contact_title']}) |" for t in top]
        return call("file_write", {"path": f"reports/sales-intel-{c.task.get('project_id', 'project')}.md", "content": "\n".join(lines)},
                    "Persist the final ranked list as a shareable report.")
    report = c.last("file_write") or {}
    return final({
        "headline": f"{score.get('qualified_count', 0)} qualified companies found; top {len(top)} prepared and approved",
        "qualified_count": score.get("qualified_count"), "candidates_evaluated": score.get("candidates_evaluated"),
        "top_opportunities": [{k: t[k] for k in ("rank", "company", "industry", "employees", "region", "rank_score",
                                                  "opportunity_score", "contact_name", "contact_title", "estimated_annual_value_usd", "rationale")} for t in top],
        "pipeline_value_usd": rank.get("pipeline_value_usd"), "crm": {k: crm.get(k) for k in ("opportunities_created", "accounts_created", "accounts_updated")},
        "outreach": {"status": send.get("status"), "sent": send.get("sent")}, "ranking_criteria": rank.get("ranking_criteria"),
        "scoring_criteria": score.get("criteria"), "report_path": report.get("path"),
    }, f"Deliverable ready: {len(top)} prioritised opportunities, CRM {crm.get('opportunities_created', 0)} created, outreach {send.get('status', 'n/a')}")


SKILLS = {
    "research_strategy": research_strategy, "discover_companies": discover_companies,
    "verify_firmographics": verify_firmographics, "enrich_companies": enrich_companies,
    "score_opportunities": score_opportunities, "identify_decision_makers": identify_decision_makers,
    "write_outreach": write_outreach, "send_outreach": send_outreach, "check_outreach": check_outreach,
    "rank_candidates": rank_candidates, "create_opportunities": create_opportunities,
    "synthesize_deliverable": synthesize_deliverable,
}
