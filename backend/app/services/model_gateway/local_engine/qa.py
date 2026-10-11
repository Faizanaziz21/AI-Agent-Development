"""QA / Critic evaluator policy. Scores a target task's output on six dimensions."""

from __future__ import annotations

import re

from app.services.model_gateway.local_engine.common import Ctx, final

DIMENSIONS = ["completeness", "evidence_quality", "factual_support", "formatting", "compliance", "instruction_adherence"]


def _outreach(out: dict, crit: dict) -> tuple[dict, list, str]:
    msgs = out.get("messages", [])
    issues = []
    unsupported = 0
    conf_sum, conf_n, personalised = 0.0, 0, 0
    for m in msgs:
        for cl in m.get("claims", []):
            conf_sum += cl["confidence"]
            conf_n += 1
            if cl["confidence"] < 0.6:
                unsupported += 1
                issues.append({"prospect_ref": m["prospect_ref"], "claim": cl["text"],
                               "reason": f"weak evidence ({cl['evidence']}, confidence {cl['confidence']:.2f})"})
        if m.get("company", "") in m.get("body", "") or m.get("industry", "") in m.get("body", ""):
            personalised += 1
    n = max(len(msgs), 1)
    flagged_msgs = len({i["prospect_ref"] for i in issues})
    scores = {
        "completeness": min(1.0, len(msgs) / max(int(crit.get("min_items", 20)), 1)),
        "evidence_quality": conf_sum / conf_n if conf_n else 0.0,
        "factual_support": 1 - flagged_msgs / n,
        "formatting": sum(1 for m in msgs if len(m.get("subject", "")) <= 90 and m.get("body", "").startswith("Hi ")) / n,
        "compliance": sum(1 for m in msgs if re.search(r"unsubscribe|reply 'stop'", m.get("body", ""), re.I)) / n,
        "instruction_adherence": personalised / n,
    }
    fb = (f"{flagged_msgs} of {len(msgs)} emails make claims not supported by sufficiently strong evidence. "
          "Remove or replace claims whose evidence confidence is below 0.7 and personalise only with verified facts.") if issues else "Claims are supported."
    # gating rule: more than 10% of messages with unsupported claims is a blocking defect for external communication
    if flagged_msgs / n > float(crit.get("max_unsupported_ratio", 0.1)):
        scores["_blocking"] = 1.0
    return scores, issues, fb


def _scoring(out: dict, crit: dict) -> tuple[dict, list, str]:
    q = out.get("qualified", [])
    issues = [{"prospect_ref": r.get("domain"), "reason": "missing score breakdown"} for r in q if not r.get("breakdown")]
    target = int(out.get("target_count") or crit.get("min_items", 50))
    scores = {"completeness": min(1.0, len(q) / max(target, 1)),
              "evidence_quality": sum(1 for r in q if r.get("provenance")) / max(len(q), 1),
              "factual_support": 1.0 if out.get("method") else 0.6, "formatting": 1 - len(issues) / max(len(q), 1),
              "compliance": 1.0, "instruction_adherence": 1.0 if out.get("criteria") else 0.5}
    fb = f"Only {len(q)}/{target} qualified companies — broaden discovery." if len(q) < target else "Scoring complete and traceable."
    return scores, issues, fb


def _support(out: dict, crit: dict) -> tuple[dict, list, str]:
    cited = out.get("citations", [])
    retrieved = set(out.get("retrieved_ids", []))
    bogus = [c for c in cited if c not in retrieved]
    reply = out.get("reply", "")
    issues = [{"reason": f"citation {b} not in retrieved sources"} for b in bogus]
    if not cited:
        issues.append({"reason": "answer has no citations to retrieved documentation"})
    if "REDACTED" in reply or re.search(r"ignore (all|previous) instructions", reply, re.I):
        issues.append({"reason": "reply contains injected or redacted content"})
    scores = {"completeness": min(1.0, len(out.get("steps", [])) / 2), "evidence_quality": min(1.0, len(cited) / 2),
              "factual_support": 0.0 if bogus else (1.0 if cited else 0.3), "formatting": 1.0 if reply.startswith(("Hi", "Hello")) else 0.5,
              "compliance": 0.0 if any("injected" in i["reason"] for i in issues) else 1.0,
              "instruction_adherence": float(out.get("confidence", 0.5))}
    return scores, issues, ("Answer grounded in retrieved documentation." if not issues else "Ground every step in a retrieved source.")


def _report(out: dict, crit: dict) -> tuple[dict, list, str]:
    rows = out.get("positioning") or out.get("findings") or out.get("obligations") or out.get("sections") or []
    unsourced = [r for r in rows if isinstance(r, dict) and not (r.get("sources") or r.get("citation") or r.get("body"))]
    issues = [{"reason": f"unsupported claim: {r.get('claim') or r.get('text', '')[:80]}"} for r in unsourced]
    n = max(len(rows), 1)
    scores = {"completeness": 1.0 if rows else 0.2, "evidence_quality": 1 - len(unsourced) / n,
              "factual_support": 1 - len(unsourced) / n, "formatting": 1.0, "compliance": 1.0,
              "instruction_adherence": 1.0 if rows else 0.4}
    return scores, issues, "Claims are sourced." if not issues else "Add sources for every claim."


def evaluate_output(c: Ctx) -> dict:
    crit = c.input.get("criteria", {}) or {}
    target = next(iter(c.deps.values()), {})
    out = target.get("output") or {}
    kind = crit.get("type", "generic")
    fn = {"outreach": _outreach, "scoring": _scoring, "support_answer": _support, "report": _report}.get(kind)
    if fn:
        scores, issues, fb = fn(out, crit)
    else:
        has = bool(out)
        scores = {d: 0.8 if has else 0.2 for d in DIMENSIONS}
        issues, fb = ([] if has else [{"reason": "empty output"}]), "Output present." if has else "Output missing."
    blocking = bool(scores.pop("_blocking", 0))
    scores = {k: round(max(0.0, min(1.0, v)), 3) for k, v in scores.items()}
    weights = crit.get("weights") or {"completeness": 1, "evidence_quality": 1, "factual_support": 1.5, "formatting": 0.5,
                                      "compliance": 1, "instruction_adherence": 1}
    overall = sum(scores[d] * weights.get(d, 1) for d in scores) / sum(weights.get(d, 1) for d in scores)
    if blocking:
        overall = min(overall, 0.65)
        fb = "BLOCKING: " + fb
    return final({"scores": scores, "overall": round(overall, 3), "issues": issues[:50], "feedback": fb,
                  "evaluated_task": target.get("title"), "criteria_type": kind},
                 f"QA score {overall:.2f} for '{target.get('title', 'output')}' — {len(issues)} issue(s)",
                 "Scored against configured review criteria.", confidence=0.85)


SKILLS = {"evaluate_output": evaluate_output}
