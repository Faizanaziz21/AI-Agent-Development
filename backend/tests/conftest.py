"""Shared fixtures. Each test session gets an isolated SQLite database seeded with both demo tenants."""

from __future__ import annotations

import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="agentos-test-")
os.environ.update({
    "AGENTOS_DATABASE_URL": f"sqlite+aiosqlite:///{_TMP}/test.db",
    "AGENTOS_OBJECT_STORAGE_PATH": f"{_TMP}/objects",
    "AGENTOS_LOCAL_MODEL_LATENCY_MS": "0",
    "AGENTOS_EMBEDDED_WORKERS": "0",
    "AGENTOS_RATE_LIMIT_PER_MINUTE": "100000",
    "AGENTOS_RECOVERY_INTERVAL_SECONDS": "1",
    "AGENTOS_SEED_DEMO": "true",
    "AGENTOS_REDIS_URL": "",
    "AGENTOS_OPENAI_API_KEY": "",
    "AGENTOS_ANTHROPIC_API_KEY": "",
})

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable  # noqa: E402
from dataclasses import dataclass  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.db import create_all, init_engine, session_scope  # noqa: E402
from app.core.events import bus  # noqa: E402
from app.core.security import Principal  # noqa: E402
from app.models import Approval, Organization, Project, User  # noqa: E402
from app.seed.seed import DEMO_PASSWORD, seed_all  # noqa: E402
from app.services import approvals, orchestrator  # noqa: E402
from app.services.agents.runtime import AgentRuntime  # noqa: E402
from app.services.model_gateway import gateway as gw_module  # noqa: E402
from app.services.model_gateway.local_engine import CONTEXT_MARKER, LocalReasoningEngine  # noqa: E402
from app.services.model_gateway.providers import LocalProvider  # noqa: E402
from app.services.model_gateway.types import ModelRequest, ModelResponse, ProviderUnavailable, estimate_tokens  # noqa: E402
from app.services.queue import InMemoryQueue, set_queue  # noqa: E402
from app.services.worker import WorkerPool  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_TMP, ignore_errors=True)


@dataclass
class Tenant:
    org_id: str
    slug: str
    users: dict[str, User]

    def principal(self, email: str) -> Principal:
        u = self.users[email]
        return Principal(u.id, u.org_id, u.role, u.email, u.name)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def database():
    init_engine()
    await create_all()
    async with session_scope() as s:
        await seed_all(s)
    bus.bind_loop(asyncio.get_running_loop())
    yield


async def _tenant(slug: str) -> Tenant:
    async with session_scope() as s:
        org = (await s.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
        users = {u.email: u for u in (await s.execute(select(User).where(User.org_id == org.id))).scalars()}
    return Tenant(org.id, slug, users)


@pytest_asyncio.fixture(scope="session")
async def sentinel() -> Tenant:
    return await _tenant("sentinel")


@pytest_asyncio.fixture(scope="session")
async def northwind() -> Tenant:
    return await _tenant("northwind")


class ScriptedProvider(LocalProvider):
    """Test double for a hosted model: returns scripted decisions per task capability and
    defers everything else to the LocalReasoningEngine. Can also simulate outages."""

    def __init__(self, name: str = "local"):
        super().__init__(name, latency_ms=0)
        self.scripts: dict[str, Callable[[dict], dict | str]] = {}
        self.fail = False
        self.calls = 0

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.fail:
            raise ProviderUnavailable(f"{self.name} is down (test)")
        user = next((m["content"] for m in reversed(request.messages) if m["role"] == "user"), "")
        idx = user.find(CONTEXT_MARKER)
        raw = json.loads(user[idx + len(CONTEXT_MARKER):]) if idx >= 0 else {}
        script = self.scripts.get((raw.get("task") or {}).get("capability", ""))
        if script is None:
            content = LocalReasoningEngine().respond(request)
        else:
            out = script(raw)
            content = out if isinstance(out, str) else json.dumps(out)
        return ModelResponse(content, model, self.name, estimate_tokens(user), estimate_tokens(content), 1)


@pytest.fixture
def scripted():
    """Install a gateway whose primary provider is scriptable; restores the default afterwards."""
    from app.services.budget import record_model_usage

    primary, backup = ScriptedProvider("local"), ScriptedProvider("local-backup")
    gw = gw_module.ModelGateway(providers={"local": primary, "local-backup": backup}, provider_order=["local", "local-backup"],
                                recorder=record_model_usage)
    previous = gw_module._gateway
    gw_module.set_gateway(gw)
    yield primary, backup, gw
    gw_module.set_gateway(previous)


@pytest_asyncio.fixture
async def pool():
    set_queue(InMemoryQueue())
    p = WorkerPool(4, name="test", runtime=AgentRuntime(), recovery_interval=0.5)
    await p.start()
    yield p
    await p.stop()
    set_queue(None)


async def wait_for_project(project_id: str, *, timeout: float = 60, approve_as: Principal | None = None,
                           decide: Callable[[Approval], tuple[str, dict | None, str]] | None = None,
                           until: tuple[str, ...] = ("COMPLETED", "FAILED", "CANCELLED")) -> Project:
    """Poll a project to a terminal state, deciding approvals along the way when a principal is given."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        decided: list[str] = []
        async with session_scope() as s:
            p = await s.get(Project, project_id)
            if p.status in until:
                return p
            if approve_as is not None:
                pending = (await s.execute(select(Approval).where(Approval.project_id == project_id,
                                                                  Approval.status == "PENDING"))).scalars().all()
                for a in pending:
                    verdict, payload, comment = decide(a) if decide else ("approve", None, "")
                    await approvals.decide(s, a, approve_as, verdict, payload, comment)
                    decided.append(a.id)
        for aid in decided:
            await orchestrator.apply_approval(aid)
        await asyncio.sleep(0.2)
    async with session_scope() as s:
        p = await s.get(Project, project_id)
    raise AssertionError(f"project {project_id} still {p.status} after {timeout}s")


@pytest_asyncio.fixture
async def client():
    from app.main import app

    app.state.ready = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def login(client: httpx.AsyncClient, email: str, password: str = DEMO_PASSWORD) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
