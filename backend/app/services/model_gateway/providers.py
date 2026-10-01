from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod

import httpx

from app.core.config import get_settings
from app.services.model_gateway.types import (
    ModelRequest,
    ModelResponse,
    ProviderBadRequest,
    ProviderRateLimited,
    ProviderUnavailable,
    estimate_tokens,
)


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    async def complete(self, model: str, request: ModelRequest) -> ModelResponse: ...


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise ProviderRateLimited(resp.text[:300])
    if resp.status_code >= 500:
        raise ProviderUnavailable(f"{resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise ProviderBadRequest(f"{resp.status_code}: {resp.text[:300]}")


class OpenAIProvider(ModelProvider):
    """Production adapter for the OpenAI Chat Completions API (JSON mode)."""

    name = "openai"

    def __init__(self, api_key: str | None, base_url: str = "https://api.openai.com/v1", timeout: float = 60):
        self.api_key, self.base_url, self.timeout = api_key, base_url, timeout

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        body: dict = {
            "model": model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.response_format == "json":
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        _raise_for_status(resp)
        data = resp.json()
        usage = data.get("usage", {})
        return ModelResponse(
            content=data["choices"][0]["message"]["content"] or "",
            model=model, provider=self.name,
            input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )


class AnthropicProvider(ModelProvider):
    """Production adapter for the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, api_key: str | None, base_url: str = "https://api.anthropic.com/v1", timeout: float = 60):
        self.api_key, self.base_url, self.timeout = api_key, base_url, timeout

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        system = "\n\n".join(m["content"] for m in request.messages if m["role"] == "system")
        msgs = [m for m in request.messages if m["role"] != "system"]
        if request.response_format == "json":
            system += "\n\nRespond with a single JSON object only."
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/messages",
                    json={"model": model, "system": system, "messages": msgs,
                          "max_tokens": request.max_output_tokens, "temperature": request.temperature},
                    headers={"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"},
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        _raise_for_status(resp)
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        return ModelResponse(
            content=text, model=model, provider=self.name,
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )


class LocalProvider(ModelProvider):
    """Credential-free provider backed by the deterministic LocalReasoningEngine.

    It reads only the prompt it is given (no database or side channels), so it exercises the
    exact same runtime contract — decision JSON, tool calls, validation — as a hosted LLM.
    """

    def __init__(self, name: str = "local", latency_ms: int | None = None):
        from app.services.model_gateway.local_engine import LocalReasoningEngine

        self.name = name
        self.engine = LocalReasoningEngine()
        self.latency_ms = get_settings().local_model_latency_ms if latency_ms is None else latency_ms

    def available(self) -> bool:
        return True

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        t0 = time.perf_counter()
        prompt_text = "".join(m["content"] for m in request.messages)
        content = self.engine.respond(request)
        if self.latency_ms:
            jitter = random.uniform(0.6, 1.4)
            scale = {"economy": 0.6, "standard": 1.0, "advanced": 1.6}.get(model.split("-")[-1], 1.0)
            await asyncio.sleep(self.latency_ms * jitter * scale / 1000)
        return ModelResponse(
            content=content, model=model, provider=self.name,
            input_tokens=estimate_tokens(prompt_text), output_tokens=estimate_tokens(content),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
