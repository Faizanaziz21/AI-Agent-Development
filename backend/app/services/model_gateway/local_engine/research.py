"""Skill policies for competitive research, document analysis and generic objectives."""

from __future__ import annotations

import re
from collections import Counter

from app.services.model_gateway.local_engine.common import Ctx, call, delegate, final

NORMALIZE_CODE = '''
rows = data["prices"]
vals = [r["price_per_endpoint_month"] for r in rows if r.get("price_per_endpoint_month")]
med = statistics.median(vals) if vals else 0
result = {"normalized": [dict(r, annual_per_endpoint=round(r["price_per_endpoint_month"] * 12, 2),
                              index_vs_median=round(r["price_per_endpoint_month"] / med, 2) if med else None)
                         for r in rows if r.get("price_per_endpoint_month")],
          "median_month": med, "min_month": min(vals) if vals else 0, "max_month": max(vals) if vals else 0}
'''


def find_competitors(c: Ctx) -> dict:
    if not c.attempted("web_search"):
        return call("web_search", {"query": c.input.get("query") or "device control endpoint vendors", "result_type": "web",
                                   "kind": "vendor", "limit": 10}, "Search for vendors in the target market.")
    r = c.last("web_search") or {}
    comps = [{"vendor": x.get("vendor") or x["title"].split(" — ")[0], "url": x["url"], "summary": x.get("snippet", "")}
             for x in r.get("results", [])]
    return final({"competitors": comps, "sources": [x["url"] for x in comps]}, f"Found {len(comps)} competitors")


def collect_pricing(c: Ctx) -> dict:
    comps = c.dep_output("competitors").get("competitors", [])
    tried = {o["arguments"].get("vendor") for o in c.attempted("web_search")}
    for comp in comps:
        if comp["vendor"] not in tried:
            return call("web_search", {"query": f"{comp['vendor']} pricing", "result_type": "web", "kind": "pricing",
                                       "vendor": comp["vendor"], "limit": 3}, f"Collect list pricing for {comp['vendor']}.")
    prices = {}
    for r in c.results("web_search"):
        for x in r.get("results", []):
            m = re.search(r"\$(\d+(?:\.\d+)?) per endpoint per month", x.get("snippet", ""))
            prices[x.get("vendor")] = {"vendor": x.get("vendor"), "price_per_endpoint_month": float(m.group(1)) if m else None,
                                       "source": x["url"], "basis": "list price" if m else "on request"}
    missing = [v for v, p in prices.items() if p["price_per_endpoint_month"] is None]
    research_children = [ch for ch in c.children if ch.get("capability") == "find_analyst_pricing"]
    analyst_children = [ch for ch in c.children if ch.get("capability") == "normalize_pricing"]
    if missing and not research_children:
        return delegate([{"title": f"Find third-party pricing evidence for {v}", "agent_key": "research",
                          "capability": "find_analyst_pricing", "input": {"vendor": v}} for v in missing],
                        f"Pricing incomplete for {len(missing)} vendors; delegating evidence gathering to other research agents.")
    for ch in research_children:
        o = ch.get("output") or {}
        if o.get("vendor") in prices and o.get("price_per_endpoint_month"):
            prices[o["vendor"]].update(price_per_endpoint_month=o["price_per_endpoint_month"], source=o["source"], basis="analyst estimate")
    if not analyst_children:
        return delegate([{"title": "Normalise competitor pricing to a common basis", "agent_key": "data_analyst",
                          "capability": "normalize_pricing", "input": {"prices": list(prices.values())}}],
                        "Asking the Data Analyst to normalise mixed list/estimated prices before submitting.")
    norm = (analyst_children[0].get("output") or {})
    return final({"pricing": list(prices.values()), "normalized": norm.get("normalized", []), "median_month": norm.get("median_month"),
                  "gaps_filled_by_delegation": len(research_children)},
                 f"Pricing for {sum(1 for p in prices.values() if p['price_per_endpoint_month'])}/{len(prices)} vendors (median ${norm.get('median_month')})")


def find_analyst_pricing(c: Ctx) -> dict:
    vendor = c.input.get("vendor")
    if not c.attempted("web_search"):
        return call("web_search", {"query": f"{vendor} quotes per endpoint", "result_type": "web", "kind": "analyst", "vendor": vendor, "limit": 3},
                    f"Look for analyst notes with pricing evidence for {vendor}.")
    for x in (c.last("web_search") or {}).get("results", []):
        m = re.search(r"\$(\d+(?:\.\d+)?) per endpoint", x.get("snippet", ""))
        if m:
            return final({"vendor": vendor, "price_per_endpoint_month": float(m.group(1)), "source": x["url"]},
                         f"{vendor}: ~${m.group(1)} per endpoint/month (analyst estimate)", confidence=0.7)
    return final({"vendor": vendor, "price_per_endpoint_month": None}, f"No pricing evidence for {vendor}", confidence=0.4)


def normalize_pricing(c: Ctx) -> dict:
    if not c.attempted("python_sandbox"):
        return call("python_sandbox", {"code": "import statistics\n" + NORMALIZE_CODE, "data": {"prices": c.input.get("prices", [])}},
                    "Normalise prices and compute market median in the sandbox.")
    r = (c.last("python_sandbox") or {}).get("result") or {}
    return final(r, f"Normalised {len(r.get('normalized', []))} prices; median ${r.get('median_month')}/endpoint/month")


def analyze_positioning(c: Ctx) -> dict:
    pricing = c.dep_output("pricing")
    comps = {x["vendor"]: x for x in c.dep_output("competitors").get("competitors", [])}
    rows = []
    for n in pricing.get("normalized", []):
        idx = n.get("index_vs_median") or 1
        tier = "premium" if idx > 1.15 else "value" if idx < 0.85 else "mid-market"
        rows.append({"vendor": n["vendor"], "price_month": n["price_per_endpoint_month"], "positioning": tier,
                     "claim": f"{n['vendor']} is positioned {tier} at ${n['price_per_endpoint_month']}/endpoint/month",
                     "sources": [n.get("source"), comps.get(n["vendor"], {}).get("url")], "basis": n.get("basis")})
    return final({"positioning": rows, "median_month": pricing.get("median_month"),
                  "insights": [f"{sum(1 for r in rows if r['positioning'] == 'premium')} premium vendors; "
                               f"market median ${pricing.get('median_month')}/endpoint/month"]},
                 f"Positioning analysed for {len(rows)} vendors")


def _citations_from(c: Ctx) -> list[dict]:
    cites = []
    for d in c.deps.values():
        o = d.get("output") or {}
        for p in o.get("passages", []) or []:
            cites.append({"id": p.get("id"), "source": p.get("source"), "section": p.get("section")})
        for u in o.get("sources", []) or []:
            cites.append({"id": f"W{len(cites) + 1}", "source": u})
    return cites


def generate_report(c: Ctx) -> dict:
    title = c.input.get("title", "Report")
    sections = []
    for key, d in c.deps.items():
        o = d.get("output") or {}
        body = d.get("output_summary") or ""
        if o.get("positioning"):
            body += "\n\n| Vendor | $/endpoint/mo | Positioning | Basis |\n|---|---|---|---|\n" + "\n".join(
                f"| {r['vendor']} | {r['price_month']} | {r['positioning']} | {r.get('basis')} |" for r in o["positioning"])
        if o.get("findings"):
            body += "\n\n" + "\n".join(f"- {f['text']} [{f.get('citation', '')}]" for f in o["findings"][:10])
        if o.get("obligations"):
            body += "\n\n" + "\n".join(f"- {x['text']} [{x.get('citation', '')}]" for x in o["obligations"][:15])
        sections.append({"heading": d.get("title", key), "body": body.strip()})
    md = f"# {title}\n\n" + "\n\n".join(f"## {s['heading']}\n\n{s['body']}" for s in sections)
    citations = _citations_from(c)
    if citations:
        md += "\n\n## Sources\n\n" + "\n".join(f"- [{x['id']}] {x['source']}" + (f" — {x['section']}" if x.get("section") else "") for x in citations)
    if "file_write" in c.tools and not c.attempted("file_write"):
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
        return call("file_write", {"path": f"reports/{slug}.md", "content": md}, "Save the report to workspace storage.")
    f = c.last("file_write") or {}
    return final({"title": title, "report_markdown": md, "sections": sections, "citations": citations, "path": f.get("path"),
                  "headline": f"{title}: {len(sections)} sections, {len(citations)} sources"},
                 f"Report '{title}' generated with {len(sections)} sections and {len(citations)} citations")


def general_research(c: Ctx) -> dict:
    q = c.input.get("query") or c.task.get("title")
    if "knowledge_search" in c.tools and not c.attempted("knowledge_search"):
        return call("knowledge_search", {"query": q[:400], "k": 6}, "Check internal knowledge first (trusted organisational sources).")
    if "web_search" in c.tools and not c.attempted("web_search"):
        return call("web_search", {"query": q[:400], "limit": 8, "result_type": "web"}, "Supplement with external sources.")
    findings = []
    for p in (c.last("knowledge_search") or {}).get("passages", []):
        sent = re.split(r"(?<=[.!?])\s+", p["text"])[0][:300]
        findings.append({"text": sent, "citation": p["id"], "source": p["source"], "kind": "internal"})
    for i, w in enumerate((c.last("web_search") or {}).get("results", []), 1):
        findings.append({"text": w.get("snippet", "")[:300], "citation": f"W{i}", "source": w.get("url"), "kind": "web"})
    return final({"findings": findings, "passages": (c.last("knowledge_search") or {}).get("passages", []),
                  "sources": [w.get("url") for w in (c.last("web_search") or {}).get("results", [])]},
                 f"{len(findings)} findings from internal and web sources", confidence=0.8 if findings else 0.4)


def analyze_findings(c: Ctx) -> dict:
    findings = c.dep_output("research").get("findings", [])
    words = Counter(w for f in findings for w in re.findall(r"[a-z]{5,}", f["text"].lower()))
    return final({"themes": [w for w, _ in words.most_common(8)], "finding_count": len(findings),
                  "internal_share": round(sum(1 for f in findings if f["kind"] == "internal") / max(len(findings), 1), 2),
                  "findings": findings},
                 f"Identified {min(8, len(words))} themes across {len(findings)} findings")


def summarize_documents(c: Ctx) -> dict:
    q = c.input.get("query") or c.task.get("title")
    if not c.attempted("knowledge_search"):
        return call("knowledge_search", {"query": q[:400], "k": 8}, "Retrieve passages from the relevant documents.")
    passages = (c.last("knowledge_search") or {}).get("passages", [])
    summary = [{"text": re.split(r"(?<=[.!?])\s+", p["text"])[0][:300], "citation": p["id"], "source": p["source"]} for p in passages]
    return final({"summary": summary, "passages": passages, "findings": summary}, f"Summarised {len(passages)} passages")


def extract_structured(c: Ctx) -> dict:
    passages = c.dep_output("retrieve").get("passages", [])
    obligations = []
    for p in passages:
        for s in re.split(r"(?<=[.!?])\s+", p["text"]):
            if re.search(r"\b(must|shall|required|within \d+|no later than|\$[\d,]+|£[\d,]+|\d+ days)\b", s, re.I):
                obligations.append({"text": s.strip()[:300], "citation": p["id"], "source": p["source"],
                                    "amounts": re.findall(r"[$£][\d,]+(?:\.\d+)?", s), "durations": re.findall(r"\d+\s+(?:days|hours|months)", s)})
    return final({"obligations": obligations, "passages": passages}, f"Extracted {len(obligations)} obligations/facts")


SKILLS = {
    "find_competitors": find_competitors, "collect_pricing": collect_pricing, "find_analyst_pricing": find_analyst_pricing,
    "normalize_pricing": normalize_pricing, "analyze_positioning": analyze_positioning, "generate_report": generate_report,
    "general_research": general_research, "analyze_findings": analyze_findings, "summarize_documents": summarize_documents,
    "extract_structured": extract_structured,
}
