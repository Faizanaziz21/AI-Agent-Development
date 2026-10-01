import pytest

from app.services.model_gateway.gateway import AllProvidersFailed, CircuitBreaker, ModelGateway, ModelRouter
from app.services.model_gateway.providers import LocalProvider, OpenAIProvider
from app.services.model_gateway.types import CallContext, ModelRequest, ProviderUnavailable
from app.services.tools.base import ToolContext, ToolPermissionDenied, ToolValidationError
from app.services.tools.builtin.data import PythonInput, PythonSandboxTool, check_code, safe_eval
from app.services.tools.builtin.research import assert_public_url

MSG = [{"role": "user", "content": "TASK_CONTEXT: {}"}]


class Down(LocalProvider):
    async def complete(self, model, request):
        raise ProviderUnavailable(f"{self.name} down")


def _router(order=("openai", "local", "local-backup"), keyed=True):
    providers = {"openai": OpenAIProvider("sk-test" if keyed else None), "local": LocalProvider("local", 0),
                 "local-backup": LocalProvider("local-backup", 0)}
    return ModelRouter(providers, list(order))


def test_router_prefers_provider_order_and_purpose_tier():
    r = _router()
    planning = r.route(ModelRequest(MSG, purpose="planning"))
    assert planning[0].name == "gpt-4o" and planning[0].tier == "advanced"
    assert [m.provider for m in planning].index("local") > 0, "secondary providers follow as fallback"
    extraction = r.route(ModelRequest(MSG, purpose="extraction"))
    assert extraction[0].tier == "economy"


def test_router_skips_unconfigured_providers():
    names = [m.provider for m in _router(keyed=False).route(ModelRequest(MSG, purpose="reasoning"))]
    assert "openai" not in names and names[0] == "local"


def test_router_budget_downgrade_and_pinning():
    r = _router()
    assert r.route(ModelRequest(MSG, purpose="planning"), downgrade=True)[0].tier == "standard"
    pinned = r.route(ModelRequest(MSG, purpose="reasoning", pinned_model="gpt-4o-mini"))
    assert pinned[0].name == "gpt-4o-mini"
    # a pinned premium model is not honoured once the budget forces a downgrade
    assert r.route(ModelRequest(MSG, purpose="reasoning", pinned_model="gpt-4o"))[0].name == "gpt-4o"
    assert r.route(ModelRequest(MSG, purpose="reasoning", pinned_model="gpt-4o"), downgrade=True)[0].tier == "economy"


def test_router_vision_and_long_context():
    r = _router()
    assert all(m.vision for m in r.route(ModelRequest(MSG, purpose="vision"))[:2])
    big = [{"role": "user", "content": "x" * 600_000}]
    assert r.route(ModelRequest(big, purpose="reasoning"))[0].long_context


def test_circuit_breaker_opens_and_half_opens(monkeypatch):
    b = CircuitBreaker()
    for _ in range(b.failure_threshold):
        b.failure()
    assert b.state == "open" and not b.allow()
    b.opened_at -= b.cooldown_s + 1
    assert b.state == "half_open" and b.allow()
    b.success()
    assert b.state == "closed"


async def test_gateway_falls_back_to_secondary_provider():
    recorded = []

    async def recorder(ctx, req, spec, resp, err, fallback_from):
        recorded.append((spec.provider, err is None, fallback_from))

    gw = ModelGateway(providers={"local": Down("local"), "local-backup": LocalProvider("local-backup", 0)},
                      provider_order=["local", "local-backup"], recorder=recorder)
    resp = await gw.complete(ModelRequest(MSG, purpose="reasoning"), CallContext(org_id="o"))
    assert resp.provider == "local-backup"
    assert resp.fallback_from is not None
    assert recorded[0][:2] == ("local", False) and recorded[-1][:2] == ("local-backup", True)


async def test_gateway_all_providers_down_raises():
    gw = ModelGateway(providers={"local": Down("local"), "local-backup": Down("local-backup")},
                      provider_order=["local", "local-backup"])
    with pytest.raises(AllProvidersFailed):
        await gw.complete(ModelRequest(MSG), CallContext(org_id="o"))
    assert gw.breakers["local"].failures >= 1


async def test_chaos_provider_failure_triggers_fallback():
    gw = ModelGateway(providers={"local": LocalProvider("local", 0), "local-backup": LocalProvider("local-backup", 0)},
                      provider_order=["local", "local-backup"])
    resp = await gw.complete(ModelRequest(MSG), CallContext(org_id="o", chaos={"provider_failure_rate": 1.0}))
    assert resp.provider == "local-backup"


# ---------------------------------------------------------------------------- tools
@pytest.mark.parametrize(("expr", "value"), [("2 + 3 * 4", 14), ("round(240 * 0.15, 2)", 36.0), ("max(1, 9, 3)", 9), ("-2 ** 2", -4)])
def test_calculator(expr, value):
    assert safe_eval(expr) == value


@pytest.mark.parametrize("expr", ["__import__('os').system('id')", "open('/etc/passwd')", "2 ** 100000", "(lambda: 1)()", "1/0"])
def test_calculator_rejects_unsafe(expr):
    with pytest.raises(ToolValidationError):
        safe_eval(expr)


@pytest.mark.parametrize("code", [
    "import os\nresult = os.listdir('/')",
    "import subprocess",
    "result = open('/etc/passwd').read()",
    "result = ().__class__.__bases__[0].__subclasses__()",
    "result = __builtins__",
    "result = getattr(1, 'real')",
])
def test_sandbox_static_guard(code):
    with pytest.raises(ToolPermissionDenied):
        check_code(code)


async def test_sandbox_executes_in_isolated_process():
    tool = PythonSandboxTool()
    ctx = ToolContext(org_id="o", agent_key="data_analyst")
    out = await tool.run(ctx, PythonInput(code="import statistics\nresult = statistics.mean(data)", data=[1, 2, 3, 6]))
    assert out["result"] == 3
    with pytest.raises(ToolValidationError):
        # runtime import guard as a second layer behind the AST check
        await tool.run(ctx, PythonInput(code="result = [x for x in data]\nraise ValueError('boom')", data=[1]))


@pytest.mark.parametrize(("url", "allow"), [
    ("http://api.github.com/repos", ["api.github.com"]),
    ("https://evil.example/steal", ["api.github.com"]),
    ("https://localhost/admin", []),
    ("https://127.0.0.1/", []),
    ("https://169.254.169.254/latest/meta-data", []),
])
def test_ssrf_guard(url, allow):
    with pytest.raises(ToolPermissionDenied):
        assert_public_url(url, allow)
