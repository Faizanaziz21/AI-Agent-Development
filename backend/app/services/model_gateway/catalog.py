"""Model catalog. Prices are USD per million tokens (list prices; override per org in settings).

`local-*` models are served by the in-process LocalReasoningEngine. Their prices mirror the
commercial model of the same tier so that cost dashboards and budget enforcement behave the
same in development as in production (cost basis is flagged as simulated in the UI).
"""

from __future__ import annotations

from app.services.model_gateway.types import ModelSpec

CATALOG: list[ModelSpec] = [
    ModelSpec("gpt-4o-mini", "openai", "economy", 128_000, 0.15, 0.60, vision=True),
    ModelSpec("gpt-4o", "openai", "advanced", 128_000, 2.50, 10.00, vision=True),
    ModelSpec("gpt-4.1", "openai", "standard", 1_000_000, 2.00, 8.00, long_context=True, general=False),
    ModelSpec("claude-3-5-haiku-latest", "anthropic", "economy", 200_000, 0.80, 4.00),
    ModelSpec("claude-sonnet-4-20250514", "anthropic", "advanced", 200_000, 3.00, 15.00, vision=True),
    ModelSpec("local-economy", "local", "economy", 32_000, 0.15, 0.60),
    ModelSpec("local-standard", "local", "standard", 128_000, 1.00, 4.00),
    ModelSpec("local-advanced", "local", "advanced", 200_000, 2.50, 10.00),
    ModelSpec("local-longctx", "local", "standard", 1_000_000, 2.00, 8.00, long_context=True, general=False),
    ModelSpec("local-vision", "local", "standard", 128_000, 2.50, 10.00, vision=True, general=False),
    ModelSpec("backup-economy", "local-backup", "economy", 32_000, 0.20, 0.80),
    ModelSpec("backup-standard", "local-backup", "standard", 128_000, 1.20, 4.80),
    ModelSpec("backup-advanced", "local-backup", "advanced", 200_000, 3.00, 12.00),
]

BY_NAME = {m.name: m for m in CATALOG}
TIERS = ["economy", "standard", "advanced"]

PURPOSE_TIER = {
    "extraction": "economy",
    "classification": "economy",
    "summarization": "economy",
    "evaluation": "standard",
    "reasoning": "standard",
    "planning": "advanced",
    "synthesis": "advanced",
}


def lower_tier(tier: str) -> str:
    i = TIERS.index(tier) if tier in TIERS else 1
    return TIERS[max(0, i - 1)]
