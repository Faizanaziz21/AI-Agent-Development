"""AI Model Gateway: routing, circuit breaking, fallback, fault injection and usage accounting."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.telemetry import MODEL_CALLS, MODEL_COST, MODEL_LATENCY, MODEL_TOKENS, span
from app.services.model_gateway.catalog import BY_NAME, CATALOG, PURPOSE_TIER, TIERS, lower_tier
from app.services.model_gateway.providers import (
    AnthropicProvider,
    LocalProvider,
    ModelProvider,
    OpenAIProvider,
)
from app.services.model_gateway.types import (
    CallContext,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    ProviderError,
    ProviderUnavailable,
    estimate_tokens,
)

log = logging.getLogger("agentos.models")

UsageRecorder = Callable[[CallContext, ModelRequest, ModelSpec, ModelResponse | None, str | None, str | None], Awaitable[None]]


class AllProvidersFailed(ProviderError):
    pass


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 20.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            return True  # half-open: let one probe through
        return False

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.allow() else "open"


class ModelRouter:
    def __init__(self, providers: dict[str, ModelProvider], provider_order: list[str]):
        self.providers = providers
        self.provider_order = provider_order

    def _usable(self, spec: ModelSpec) -> bool:
        p = self.providers.get(spec.provider)
        return p is not None and p.available()

    def route(self, req: ModelRequest, downgrade: bool = False) -> list[ModelSpec]:
        tier = req.tier or PURPOSE_TIER.get(req.purpose, "standard")
        if req.purpose == "planning" and not req.tier:
            tier = "advanced"
        if downgrade:
            tier = lower_tier(tier)
        prompt_tokens = estimate_tokens("".join(m["content"] for m in req.messages)) + req.max_output_tokens
        needs_vision = req.purpose == "vision" or bool(req.images)

        def eligible(m: ModelSpec) -> bool:
            return self._usable(m) and m.context_window >= prompt_tokens and (m.vision or not needs_vision)

        order = {p: i for i, p in enumerate(self.provider_order)}
        rank = lambda m: (order.get(m.provider, 99), TIERS.index(m.tier))  # noqa: E731
        candidates: list[ModelSpec] = []

        if req.pinned_model and req.pinned_model in BY_NAME and eligible(BY_NAME[req.pinned_model]) and not downgrade:
            candidates.append(BY_NAME[req.pinned_model])
        if needs_vision:
            candidates += sorted([m for m in CATALOG if m.vision and eligible(m)], key=rank)
        elif prompt_tokens > 100_000 or req.purpose == "long_context":
            candidates += sorted([m for m in CATALOG if m.long_context and eligible(m)], key=rank)
        # same tier across providers (primary → secondary), then cheaper tiers as last resort
        candidates += sorted([m for m in CATALOG if m.tier == tier and m.general and eligible(m)], key=rank)
        for t in reversed(TIERS[: TIERS.index(tier)]):
            candidates += sorted([m for m in CATALOG if m.tier == t and m.general and eligible(m)], key=rank)
        seen, out = set(), []
        for m in candidates:
            if m.name not in seen:
                seen.add(m.name)
                out.append(m)
        return out


class ModelGateway:
    def __init__(self, providers: dict[str, ModelProvider] | None = None, provider_order: list[str] | None = None,
                 recorder: UsageRecorder | None = None):
        s = get_settings()
        if providers is None:
            providers = {
                "openai": OpenAIProvider(s.openai_api_key),
                "anthropic": AnthropicProvider(s.anthropic_api_key),
                "local": LocalProvider("local"),
                "local-backup": LocalProvider("local-backup"),
            }
        if provider_order is None:
            provider_order = [p.strip() for p in s.provider_order.split(",") if p.strip()]
            for extra in ("local", "local-backup"):
                if extra not in provider_order:
                    provider_order.append(extra)
        self.providers = providers
        self.router = ModelRouter(providers, provider_order)
        self.breakers: dict[str, CircuitBreaker] = {name: CircuitBreaker() for name in providers}
        self.recorder = recorder

    def status(self) -> list[dict]:
        return [
            {"provider": n, "available": p.available(), "circuit": self.breakers[n].state}
            for n, p in self.providers.items()
        ]

    async def complete(self, req: ModelRequest, ctx: CallContext) -> ModelResponse:
        candidates = self.router.route(req, downgrade=ctx.downgrade)
        if not candidates:
            raise AllProvidersFailed("no eligible model for request")
        chaos = ctx.chaos or {}
        primary_provider = candidates[0].provider
        first_failed: str | None = None
        errors: list[str] = []
        for spec in candidates:
            breaker = self.breakers.setdefault(spec.provider, CircuitBreaker())
            if not breaker.allow():
                errors.append(f"{spec.provider}: circuit open")
                continue
            provider = self.providers[spec.provider]
            with span("model.complete", provider=spec.provider, model=spec.name, purpose=req.purpose, agent=ctx.agent_key):
                t0 = time.perf_counter()
                try:
                    if spec.provider == primary_provider and random.random() < float(chaos.get("provider_failure_rate", 0)):
                        raise ProviderUnavailable(f"simulated outage of provider '{spec.provider}'")
                    resp = await provider.complete(spec.name, req)
                except ProviderError as exc:
                    elapsed = time.perf_counter() - t0
                    breaker.failure()
                    MODEL_CALLS.labels(spec.provider, spec.name, "error").inc()
                    MODEL_LATENCY.labels(spec.provider, spec.name).observe(elapsed)
                    errors.append(f"{spec.name}: {exc}")
                    log.warning("model call failed provider=%s model=%s err=%s", spec.provider, spec.name, exc)
                    if self.recorder:
                        failed = ModelResponse("", spec.name, spec.provider, estimate_tokens(str(req.messages)), 0, int(elapsed * 1000))
                        await self.recorder(ctx, req, spec, failed, str(exc), first_failed)
                    first_failed = first_failed or spec.name
                    if not exc.transient:
                        raise
                    continue
            breaker.success()
            if random.random() < float(chaos.get("malformed_output_rate", 0)):
                resp.content = resp.content[: max(1, len(resp.content) // 3)] + " <<truncated"
            resp.cost_usd = spec.cost(resp.input_tokens, resp.output_tokens)
            resp.fallback_from = first_failed
            resp.downgraded = ctx.downgrade
            MODEL_CALLS.labels(spec.provider, spec.name, "ok").inc()
            MODEL_LATENCY.labels(spec.provider, spec.name).observe(resp.latency_ms / 1000)
            MODEL_TOKENS.labels(spec.provider, spec.name, "in").inc(resp.input_tokens)
            MODEL_TOKENS.labels(spec.provider, spec.name, "out").inc(resp.output_tokens)
            MODEL_COST.labels(spec.provider, spec.name).inc(resp.cost_usd)
            if self.recorder:
                await self.recorder(ctx, req, spec, resp, None, first_failed)
            return resp
        raise AllProvidersFailed("; ".join(errors))


_gateway: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        from app.services.budget import record_model_usage

        _gateway = ModelGateway(recorder=record_model_usage)
    return _gateway


def set_gateway(gw: ModelGateway | None) -> None:
    global _gateway
    _gateway = gw
