"""Skill policies for the Autonomous Customer Support Team."""

from __future__ import annotations

import re

from app.services.model_gateway.local_engine.common import Ctx, call, final

CATEGORIES = [
    ("data_incident", ["breach", "leak", "exfiltrat", "stolen", "data loss", "incident"]),
    ("billing", ["refund", "invoice", "charge", "billing", "overcharg", "credit"]),
    ("account_access", ["login", "sso", "password", "locked out", "mfa", "saml"]),
    ("integration", ["api", "intune", "siem", "webhook", "integration", "sync"]),
    ("device_blocking", ["usb", "blocked", "device", "peripheral", "printer", "bluetooth", "removable"]),
]
NEGATIVE = ["unacceptable", "furious", "angry", "terrible", "cancel", "frustrat", "disappointed", "worst", "again"]
LEGAL = ["lawyer", "legal action", "solicitor", "sue", "gdpr complaint", "ico", "regulator"]
URGENT = ["urgent", "asap", "outage", "down", "production", "critical", "immediately"]


def _words(terms: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(map(re.escape, terms)) + r")\b")


# Short terms such as "sue" and "ico" must not match inside "issue" or "device".
LEGAL_RE = _words(LEGAL + ["sued", "suing"])
URGENT_RE = _words(URGENT)


def ticket(c: Ctx) -> dict:
    t = c.params.get("ticket") or {}
    return t.get("content", t) if isinstance(t, dict) else {}


def _refund_amount(text: str) -> float:
    if "refund" not in text.lower():
        return 0.0
    m = re.findall(r"(?:\$|usd\s?|£)\s?([\d,]+(?:\.\d{1,2})?)", text, re.I)
    return max((float(x.replace(",", "")) for x in m), default=0.0)


def triage_ticket(c: Ctx) -> dict:
    t = ticket(c)
    text = f"{t.get('subject', '')} {t.get('body', '')}".lower()
    category = next((name for name, kws in CATEGORIES if any(k in text for k in kws)), "general")
    sentiment = "negative" if sum(w in text for w in NEGATIVE) >= 1 else "neutral"
    urgency = "urgent" if URGENT_RE.search(text) else "high" if sentiment == "negative" else "normal"
    product_area = {"device_blocking": "Device Policy Engine", "integration": "Integrations", "account_access": "Identity & SSO",
                    "billing": "Billing", "data_incident": "Security Operations"}.get(category, "General")
    if not c.attempted("ticket_update"):
        return call("ticket_update", {"ticket_id": t.get("ticket_id"), "status": "triaged", "category": category,
                                      "priority": urgency if urgency != "normal" else "normal", "product_area": product_area},
                    f"Classified as {category} ({urgency}, {sentiment} sentiment); recording triage on the ticket.")
    keywords = [w for w in re.findall(r"[a-z]{4,}", text) if w not in {"with", "that", "this", "have", "from", "please", "thanks"}][:12]
    return final({"ticket_id": t.get("ticket_id"), "category": category, "urgency": urgency, "sentiment": sentiment,
                  "product_area": product_area, "customer_email": t.get("customer_email"),
                  "refund_requested_usd": _refund_amount(f"{t.get('subject', '')} {t.get('body', '')}"),
                  "legal_language": bool(LEGAL_RE.search(text)), "search_query": f"{t.get('subject', '')} {' '.join(keywords[:8])}"},
                 f"Triage: {category}, {urgency}, sentiment {sentiment}")


def lookup_account(c: Ctx) -> dict:
    tri = c.dep_output("triage")
    email = tri.get("customer_email") or ticket(c).get("customer_email")
    if not c.attempted("crm_search"):
        return call("crm_search", {"email": email}, "Look up the customer's account, tier and open opportunities by email domain.")
    r = c.last("crm_search") or {}
    acct = (r.get("accounts") or [None])[0]
    if not acct:
        return final({"found": False, "tier": "unknown"}, "No matching CRM account", confidence=0.6)
    return final({"found": True, "account_id": acct["id"], "name": acct["name"], "tier": acct.get("tier"),
                  "arr_usd": (acct.get("attributes") or {}).get("arr_usd"), "lifecycle_stage": acct.get("lifecycle_stage"),
                  "contacts": acct.get("contacts", [])[:3]},
                 f"Account {acct['name']} ({acct.get('tier')} tier)")


def retrieve_docs(c: Ctx) -> dict:
    tri = c.dep_output("triage")
    if not c.attempted("knowledge_search"):
        return call("knowledge_search", {"query": tri.get("search_query") or ticket(c).get("subject", "help"), "k": 5},
                    "Retrieve documentation passages relevant to the ticket with hybrid search.")
    r = c.last("knowledge_search") or {}
    passages = r.get("passages", [])
    flagged = [p["id"] for p in passages if p.get("injection_risk", 0) >= 0.5]
    return final({"passages": passages, "citations": [{k: p[k] for k in ("id", "source", "section", "chunk_id")} for p in passages],
                  "flagged_passages": flagged, "access_scope": r.get("access_scope")},
                 f"Retrieved {len(passages)} passages" + (f"; {len(flagged)} contained instruction-like content (neutralised)" if flagged else ""),
                 confidence=0.85 if passages else 0.3)


_STEP = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*])\s+(.{10,240})$")


def propose_solution(c: Ctx) -> dict:
    tri, acct, kb = c.dep_output("triage"), c.dep_output("account"), c.dep_output("knowledge")
    t = ticket(c)
    passages = [p for p in kb.get("passages", []) if p.get("injection_risk", 0) < 0.5] or kb.get("passages", [])
    steps, cited = [], []
    for p in passages[:3]:
        found = [s.strip() for s in _STEP.findall(p.get("text", "")) if "REDACTED" not in s]
        if not found:
            found = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p.get("text", "")) if 30 < len(s) < 240 and "REDACTED" not in s][:2]
        for s in found[:3]:
            steps.append(f"{s} [{p['id']}]")
        if found:
            cited.append(p["id"])
    name = (acct.get("contacts") or [{}])[0].get("name", "").split(" ")[0] if acct.get("found") else ""
    greeting = f"Hi {name}," if name else "Hello,"
    if tri.get("category") == "billing" and tri.get("refund_requested_usd"):
        steps.insert(0, f"We have reviewed your request for a refund of ${tri['refund_requested_usd']:,.2f} under our refund policy [{cited[0] if cited else 'S1'}].")
    body = "\n".join([greeting, "", f"Thanks for contacting us about \"{t.get('subject', 'your issue')}\". Here is what we recommend:", ""]
                     + [f"{i}. {s}" for i, s in enumerate(steps[:6], 1)]
                     + ["", "If this doesn't resolve the issue, reply to this email and our engineers will follow up.",
                        "", "Kind regards,", "AgentOS Support Team"])
    confidence = 0.85 if len(cited) >= 2 else 0.65 if cited else 0.35
    return final({"reply": body, "steps": steps[:6], "citations": cited, "confidence": confidence,
                  "refund_requested_usd": tri.get("refund_requested_usd", 0), "retrieved_ids": [p["id"] for p in kb.get("passages", [])]},
                 f"Proposed {len(steps[:6])}-step solution citing {', '.join(cited) or 'no sources'}", confidence=confidence)


def evaluate_risk(c: Ctx) -> dict:
    tri, acct, sol = c.dep_output("triage"), c.dep_output("account"), c.dep_output("solution")
    factors, score = [], 0.0
    refund = float(tri.get("refund_requested_usd") or 0)
    if refund > 1000:
        factors.append(f"refund ${refund:,.0f} exceeds $1,000"); score += 0.5
    elif refund > 0:
        factors.append(f"refund requested (${refund:,.0f})"); score += 0.25
    if tri.get("legal_language"):
        factors.append("legal / regulatory language"); score += 0.5
    if tri.get("category") == "data_incident":
        factors.append("possible security incident"); score += 0.5
    if acct.get("tier") == "enterprise" and tri.get("sentiment") == "negative":
        factors.append("negative sentiment from enterprise account"); score += 0.3
    if float(sol.get("confidence", 0)) < 0.6:
        factors.append("low solution confidence"); score += 0.3
    if c.dep_output("knowledge").get("flagged_passages"):
        factors.append("retrieved content contained injection attempts"); score += 0.2
    level = "high" if score >= 0.5 else "medium" if score >= 0.25 else "low"
    return final({"risk_level": level, "risk_score": round(min(score, 1.0), 2), "factors": factors,
                  "requires_human_review": level == "high", "refund_requested_usd": refund},
                 f"Risk {level} ({', '.join(factors) or 'no risk factors'})",
                 "High-risk responses are routed to a human by policy; low-risk can auto-send.")


def respond_to_customer(c: Ctx) -> dict:
    tri, sol, risk = c.dep_output("triage"), c.dep_output("solution"), c.dep_output("risk")
    t = ticket(c)
    to = tri.get("customer_email") or t.get("customer_email")
    refund = float(risk.get("refund_requested_usd") or 0)
    rej = c.human_decision("email_send")
    if rej:
        return final({"responded": False, "status": "held_by_reviewer", "feedback": rej.get("error")}, "Reply held by human reviewer")
    if refund and "refund_issue" in c.tools and not c.attempted("refund_issue"):
        return call("refund_issue", {"ticket_id": t.get("ticket_id"), "account_email": to, "amount_usd": refund,
                                     "reason": f"Customer request on ticket {t.get('ticket_id')}"},
                    "Refund requested; billing policy decides whether finance approval is needed.")
    if not c.results("email_send"):
        return call("email_send", {"messages": [{"to": to, "subject": f"Re: {t.get('subject', 'your request')}"[:300], "body": sol.get("reply", "")}],
                                   "risk_level": risk.get("risk_level", "medium"), "purpose": "support_reply"},
                    f"Send the QA-verified reply (risk {risk.get('risk_level')}); policy routes high risk to human approval.")
    r = c.last("email_send") or {}
    refund_res = c.last("refund_issue")
    return final({"responded": True, "sent": r.get("sent", 0), "risk_level": risk.get("risk_level"),
                  "refund": refund_res, "status": "resolved"},
                 f"Customer reply sent (risk {risk.get('risk_level')})" + (f"; refund {refund_res.get('refund_id')} issued" if refund_res else ""))


def summarize_resolution(c: Ctx) -> dict:
    tri, sol, risk, resp = (c.dep_output(k) for k in ("triage", "solution", "risk", "respond"))
    t = ticket(c)
    issue_type = {"device_blocking": "configuration", "integration": "integration defect", "account_access": "identity configuration",
                  "billing": "commercial", "data_incident": "security event"}.get(tri.get("category"), "how-to")
    summary = (f"Customer reported: {t.get('subject')}. Classified {tri.get('category')} / {tri.get('product_area')} "
               f"({tri.get('urgency')}). Proposed {len(sol.get('steps', []))}-step fix citing {', '.join(sol.get('citations', [])) or 'none'}. "
               f"Risk {risk.get('risk_level')}; reply {'sent' if resp.get('responded') else 'held for review'}.")
    insight = (f"Tickets about '{t.get('subject', '')[:60]}' in {tri.get('product_area')} are resolved by: "
               + "; ".join(s.split(' [')[0] for s in sol.get("steps", [])[:2]))
    return final({"summary": summary, "classification": {"product_area": tri.get("product_area"), "issue_type": issue_type,
                                                         "category": tri.get("category")},
                  "knowledge_insight": insight, "evidence": sol.get("citations", [])},
                 "Resolution summarised and product issue classified")


def finalize_ticket(c: Ctx) -> dict:
    tri, summ, risk, resp = (c.dep_output(k) for k in ("triage", "summary", "risk", "respond"))
    t = ticket(c)
    if not c.attempted("ticket_update"):
        return call("ticket_update", {"ticket_id": t.get("ticket_id"), "status": "resolved" if resp.get("responded") else "escalated",
                                      "summary": summ.get("summary"), "resolution": "; ".join(c.dep_output("solution").get("steps", []))[:8000],
                                      "risk_level": risk.get("risk_level"), "product_area": summ.get("classification", {}).get("product_area")},
                    "Close out the ticket with summary, resolution and risk classification.")
    if "memory_write" in c.tools and not c.attempted("memory_write"):
        return call("memory_write", {"content": summ.get("knowledge_insight", ""), "kind": "insight", "key": f"support:{tri.get('category')}",
                                     "scope": "long_term", "confidence": 0.7, "evidence": summ.get("evidence", [])},
                    "Propose a reusable knowledge insight (stored unverified until a human approves).")
    if not c.attempted("analytics_event"):
        return call("analytics_event", {"name": "support.ticket_resolved", "properties": {
            "ticket_id": t.get("ticket_id"), "category": tri.get("category"), "risk": risk.get("risk_level"),
            "auto_resolved": risk.get("risk_level") != "high", "product_area": summ.get("classification", {}).get("product_area")}},
                    "Emit analytics for support reporting.")
    return final({"ticket_id": t.get("ticket_id"), "status": "resolved" if resp.get("responded") else "escalated",
                  "summary": summ.get("summary"), "classification": summ.get("classification"), "risk": risk,
                  "knowledge_insight": summ.get("knowledge_insight"), "reply_sent": resp.get("responded", False)},
                 "Ticket closed out; insight proposed; analytics recorded")


SKILLS = {
    "triage_ticket": triage_ticket, "lookup_account": lookup_account, "retrieve_docs": retrieve_docs,
    "propose_solution": propose_solution, "evaluate_risk": evaluate_risk, "respond_to_customer": respond_to_customer,
    "summarize_resolution": summarize_resolution, "finalize_ticket": finalize_ticket,
}
