"""HTTP API: authentication, RBAC, tenant isolation, validation, webhooks, knowledge, audit."""

import json
import time

from app.core.security import sign_webhook
from tests.conftest import login
from tests.integration.helpers import final, make_project, tool, wait_task

SENTINEL_SECRET = "whsec_sentinel_demo_secret"


def _signed(body: dict, secret: str = SENTINEL_SECRET, ts: int | None = None) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    stamp = str(int(ts if ts is not None else time.time()))
    return raw, {"X-AgentOS-Timestamp": stamp, "X-AgentOS-Signature": sign_webhook(secret, raw, stamp),
                 "Content-Type": "application/json"}


async def test_login_rejects_bad_credentials_and_unauthenticated_calls(client):
    r = await client.post("/api/v1/auth/login", json={"email": "admin@sentinel.example", "password": "wrong"})
    assert r.status_code == 401
    assert (await client.get("/api/v1/projects")).status_code == 401
    assert (await client.get("/api/v1/projects", headers={"Authorization": "Bearer not-a-token"})).status_code == 401
    me = await client.get("/api/v1/auth/me", headers=await login(client, "admin@sentinel.example"))
    assert me.json()["user"]["role"] == "owner"


async def test_rbac_viewer_is_read_only(client):
    h = await login(client, "viewer@sentinel.example")
    assert (await client.get("/api/v1/projects", headers=h)).status_code == 200
    r = await client.post("/api/v1/projects", headers=h, json={"name": "x", "objective": "a valid objective text"})
    assert r.status_code == 403
    assert (await client.get("/api/v1/audit", headers=h)).status_code == 403
    assert (await client.put("/api/v1/secrets", headers=h, json={"name": "k", "value": "v"})).status_code == 403


async def test_request_validation(client):
    h = await login(client, "admin@sentinel.example")
    bad = [
        {"name": "x", "objective": "short"},
        {"name": "x", "objective": "a valid objective text", "budget_usd": -1},
        {"name": "x", "objective": "a valid objective text", "chaos": {"meteor_strike": 0.5}},
        {"name": "x", "objective": "a valid objective text", "chaos": {"worker_crash_rate": 2}},
        {"name": "x", "objective": "a valid objective text", "template": "nope"},
    ]
    for body in bad:
        r = await client.post("/api/v1/projects", headers=h, json={**body, "start": False})
        assert r.status_code == 422, body


async def test_tenant_isolation_across_resources(client, sentinel, northwind, scripted):
    primary, *_ = scripted
    primary.scripts["t_iso"] = lambda raw: final({"secret": "sentinel-only"})
    pid, ids = await make_project(sentinel.org_id, [{"key": "t", "agent": "data_analyst", "capability": "t_iso"}], name="isolated")
    other = await login(client, "admin@northwind.example")
    for path in (f"/api/v1/projects/{pid}", f"/api/v1/projects/{pid}/graph", f"/api/v1/projects/{pid}/replay",
                 f"/api/v1/tasks/{ids['t']}"):
        assert (await client.get(path, headers=other)).status_code == 404, path
    assert (await client.post(f"/api/v1/projects/{pid}/cancel", headers=other)).status_code == 404
    assert pid not in {p["id"] for p in (await client.get("/api/v1/projects", headers=other)).json()}

    sentinel_sources = (await client.get("/api/v1/knowledge/sources", headers=await login(client, "admin@sentinel.example"))).json()
    assert sentinel_sources
    assert (await client.get(f"/api/v1/knowledge/sources/{sentinel_sources[0]['id']}", headers=other)).status_code == 404
    hits = (await client.post("/api/v1/knowledge/search", headers=other, json={"query": "refund policy"})).json()["results"]
    sentinel_names = {s["name"] for s in sentinel_sources}
    assert not any(h["source"] in sentinel_names for h in hits)
    accounts = (await client.get("/api/v1/crm/accounts", headers=other)).json()
    assert not any("Harbour Health" in a["name"] for a in accounts)
    await wait_task(ids["t"], {"QUEUED", "RUNNING", "COMPLETED", "READY"})


async def test_approval_decision_via_api_enforces_role_and_tenant(client, sentinel, scripted, pool):
    primary, *_ = scripted
    primary.scripts["t_refund_api"] = lambda raw: final({"done": raw["observations"][-1]["status"]}) if raw.get("observations") else tool(
        "refund_issue", {"account_email": "priya.shah@harbourhealthclinics.example", "amount_usd": 2400, "reason": "api test"})
    pid, ids = await make_project(sentinel.org_id, [{"key": "r", "agent": "support_triage", "capability": "t_refund_api"}])
    await wait_task(ids["r"], {"WAITING_APPROVAL"})
    admin = await login(client, "admin@sentinel.example")
    pending = [a for a in (await client.get("/api/v1/approvals?status=PENDING", headers=admin)).json() if a["task_id"] == ids["r"]]
    aid = pending[0]["id"]
    assert (await client.post(f"/api/v1/approvals/{aid}/decision", headers=await login(client, "viewer@sentinel.example"),
                              json={"decision": "approve"})).status_code == 403
    assert (await client.post(f"/api/v1/approvals/{aid}/decision", headers=await login(client, "admin@northwind.example"),
                              json={"decision": "approve"})).status_code == 404
    assert (await client.post(f"/api/v1/approvals/{aid}/decision", headers=await login(client, "approver@sentinel.example"),
                              json={"decision": "approve"})).status_code == 403
    r = await client.post(f"/api/v1/approvals/{aid}/decision", headers=await login(client, "finance@sentinel.example"),
                          json={"decision": "approve", "comment": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert (await client.post(f"/api/v1/approvals/{aid}/decision", headers=admin, json={"decision": "approve"})).status_code == 409
    t = await wait_task(ids["r"], {"COMPLETED"})
    assert t.output == {"done": "SUCCESS"}


async def test_signed_webhook_ingestion(client, pool):
    payload = {"external_id": "HD-9001", "customer_email": "tom.reid@quaysideretail.example", "subject": "Login trouble",
               "body": "Our staff cannot sign in to the Sentinel portal since this morning."}
    raw, headers = _signed(payload)
    r = await client.post("/api/v1/webhooks/support/sentinel", content=raw, headers=headers)
    assert r.status_code == 202 and r.json()["status"] == "accepted"
    dup = await client.post("/api/v1/webhooks/support/sentinel", content=raw, headers=headers)
    assert dup.json()["status"] == "duplicate" and dup.json()["ticket_id"] == r.json()["ticket_id"]

    raw2, h2 = _signed({**payload, "external_id": "HD-9002"}, secret="wrong")
    assert (await client.post("/api/v1/webhooks/support/sentinel", content=raw2, headers=h2)).status_code == 401
    raw3, h3 = _signed({**payload, "external_id": "HD-9003"}, ts=int(time.time()) - 3600)
    assert (await client.post("/api/v1/webhooks/support/sentinel", content=raw3, headers=h3)).status_code == 401, "stale replay"
    raw4, h4 = _signed(payload)
    assert (await client.post("/api/v1/webhooks/support/unknown-org", content=raw4, headers=h4)).status_code == 401
    tampered = raw.replace(b"Login trouble", b"Refund me now")
    assert (await client.post("/api/v1/webhooks/support/sentinel", content=tampered, headers=headers)).status_code == 401


async def test_knowledge_upload_search_and_classification(client):
    admin = await login(client, "admin@sentinel.example")
    doc = (b"# Warranty Terms\n\nSentinel hardware carries a 36 month warranty. Batteries are covered for 12 months.\n\n"
           b"## Exclusions\n\nLiquid damage voids the warranty.")
    r = await client.post("/api/v1/knowledge/sources", headers=admin, files={"file": ("warranty.md", doc, "text/markdown")},
                          data={"classification": "confidential", "tags": "warranty,legal-review"})
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["status"] == "INDEXED" and src["chunk_count"] >= 1
    hits = (await client.post("/api/v1/knowledge/search", headers=admin, json={"query": "how long is the battery warranty"})).json()
    assert hits["results"][0]["source"] == "warranty.md"

    viewer = await login(client, "viewer@sentinel.example")
    vh = (await client.post("/api/v1/knowledge/search", headers=viewer, json={"query": "battery warranty months"})).json()["results"]
    assert all(h["source"] != "warranty.md" for h in vh), "confidential documents are hidden from viewers"
    assert (await client.get(f"/api/v1/knowledge/sources/{src['id']}", headers=viewer)).status_code == 403

    bad = await client.post("/api/v1/knowledge/sources", headers=admin, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 415

    ans = (await client.post("/api/v1/knowledge/ask", headers=admin, json={"question": "How long are batteries covered?"})).json()
    assert ans["citations"], "answers are grounded in cited passages"
    assert set(ans["citations"]) <= {p["id"] for p in ans["passages"]}


async def test_audit_chain_verifies(client):
    admin = await login(client, "admin@sentinel.example")
    r = (await client.get("/api/v1/audit/verify", headers=admin)).json()
    assert r["valid"] is True and r["records"] > 0


async def test_health_and_metrics(client):
    assert (await client.get("/healthz")).status_code == 200
    m = await client.get("/metrics")
    assert m.status_code == 200 and "agentos_http_requests_total" in m.text
