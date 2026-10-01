"""LocalReasoningEngine — deterministic, credential-free model implementation.

It consumes the same prompt the runtime sends to hosted LLMs (system rules + TASK_CONTEXT JSON)
and returns a decision JSON obeying DECISION_SCHEMA. Skill policies are keyed by task
capability; unknown capabilities fall back to a generic retrieve-then-summarise policy, so
administrator-defined agents work out of the box.
"""

from __future__ import annotations

import json
import re

from app.services.model_gateway.local_engine import planning, qa, research, sales, support
from app.services.model_gateway.local_engine.common import Ctx, call, escalate, final
from app.services.rag.embeddings import _stem, tokenize

CONTEXT_MARKER = "TASK_CONTEXT:"

SKILLS = {**sales.SKILLS, **support.SKILLS, **research.SKILLS, **qa.SKILLS}


def generic(c: Ctx) -> dict:
    query = c.input.get("query") or f"{c.task.get('title', '')} {c.task.get('description', '')}".strip()
    if "knowledge_search" in c.tools and not c.attempted("knowledge_search"):
        return call("knowledge_search", {"query": query[:400] or "overview", "k": 5},
                    "Gather relevant organisational knowledge for this task.")
    passages = (c.last("knowledge_search") or {}).get("passages", [])
    dep_summaries = {k: d.get("output_summary", "") for k, d in c.deps.items()}
    return final({"summary": f"{c.task.get('title')}: completed using {len(passages)} knowledge passages and "
                             f"{len(dep_summaries)} upstream results.",
                  "findings": [{"text": p["text"][:300], "citation": p["id"]} for p in passages],
                  "inputs": dep_summaries}, f"Completed '{c.task.get('title')}'", confidence=0.7 if passages else 0.5)


class LocalReasoningEngine:
    def respond(self, request) -> str:
        user = next((m["content"] for m in reversed(request.messages) if m["role"] == "user"), "")
        idx = user.find(CONTEXT_MARKER)
        if idx < 0:
            return json.dumps(escalate("Request did not include TASK_CONTEXT; cannot act safely."))
        try:
            raw = json.loads(user[idx + len(CONTEXT_MARKER):].strip())
        except json.JSONDecodeError:
            return json.dumps(escalate("TASK_CONTEXT was not valid JSON."))
        if raw.get("mode") == "planning":
            return json.dumps(planning.plan_objective(raw))
        if raw.get("mode") == "answer":
            return json.dumps(self._answer(raw))
        c = Ctx(raw)
        skill = SKILLS.get(c.task.get("capability", ""), generic)
        decision = skill(c)
        if decision.get("action") == "tool_call" and decision["tool"] not in c.tools:
            # never attempt a tool the agent is not granted — degrade to a summary instead
            return json.dumps(final({"note": f"tool {decision['tool']} unavailable to this agent", "partial": True},
                                    "Completed with reduced capability", confidence=0.4))
        return json.dumps(decision)

    @staticmethod
    def _answer(raw: dict) -> dict:
        q = raw.get("question", "")
        passages = raw.get("passages", [])
        if not passages:
            return {"answer": "I could not find this in the knowledge base.", "citations": [], "confidence": 0.2}
        qt = {_stem(t) for t in tokenize(q)}
        sentences = []
        for rank, p in enumerate(passages):
            for s in re.split(r"(?<=[.!?])\s+|\n+", p["text"]):
                s = s.strip(" -*#")
                if len(s) < 25 or "REDACTED" in s:
                    continue
                overlap = len(qt & {_stem(t) for t in tokenize(s)})
                # prefer sentences from higher-ranked passages, then document order
                sentences.append((overlap - rank * 0.1, -len(sentences), s, p["id"]))
        sentences.sort(reverse=True)
        picked = [x for x in sentences if x[0] > 0][:4] or sentences[:2]
        answer = " ".join(f"{s} [{pid}]" for _, _, s, pid in picked)
        cites = sorted({pid for *_, pid in picked})
        return {"answer": answer, "citations": cites, "confidence": 0.85 if len(picked) >= 2 else 0.6}


__all__ = ["LocalReasoningEngine", "CONTEXT_MARKER"]
