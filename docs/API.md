# AgentOS — HTTP API Reference

The API is served by FastAPI. Interactive OpenAPI documentation is available from a running
server at `/docs` (Swagger UI) and `/openapi.json`; this page summarises the endpoints, their
permissions and the conventions they share.

Base path: `/api/v1` (health and metrics endpoints are at the root).

## Conventions

**Authentication.** Obtain a token from `POST /auth/login` and send it as
`Authorization: Bearer <token>`. Server-sent event streams also accept `?access_token=<token>`
because browsers' `EventSource` cannot set headers. Tokens are HS256 JWTs valid for
`AGENTOS_JWT_TTL_MINUTES` (12 hours by default). The user's role and active flag are re-read on
every request.

**Permissions.** Each endpoint below lists the permission it requires (see the role matrix in
[SECURITY.md](SECURITY.md#52-roles)). "auth" means any signed-in user; "public" means no token.

**Tenancy.** Every query is scoped to the caller's organisation. Resources belonging to another
organisation return `404`, never `403`.

**Errors.** Errors are JSON objects with a `detail` field — a string, or for request validation
errors (`422`) a list of `{type, loc, msg, input}` objects:

```json
{"detail": [{"type": "missing", "loc": ["body", "objective"], "msg": "Field required", "input": {"name": "x"}}]}
```

| Status | Meaning |
|---|---|
| 400 | Request is well-formed but not acceptable (for example a missing comment on `request_changes`) |
| 401 | Missing, invalid or expired token; deactivated user; bad credentials; bad webhook signature |
| 403 | Signed in but lacking the permission or approver role |
| 404 | Not found or owned by another organisation |
| 409 | State conflict (approval already decided, project already running, duplicate key or email) |
| 413 / 415 | Upload too large (> 20 MB) / unsupported file type |
| 422 | Validation error |
| 429 | Rate limit or sign-in throttle; see `Retry-After` |

**Rate limiting.** Responses carry `X-RateLimit-Limit` and `X-RateLimit-Remaining`. Exceeding
`AGENTOS_RATE_LIMIT_PER_MINUTE` (600 per user per minute) returns `429` with `Retry-After`.

**Identifiers and times.** IDs are 32-character hex strings. Timestamps are ISO 8601 UTC.
Money is in US dollars as floating-point numbers.

## Authentication

| Method | Path | Permission | Description |
|---|---|---|---|
| POST | `/auth/login` | public | Exchange `{email, password}` for `{access_token, token_type, user, organization}` |
| GET | `/auth/me` | auth | Current `user`, `organization` and the caller's `permissions` |

After `AGENTOS_LOGIN_MAX_FAILURES` (10) failed attempts for an email within
`AGENTOS_LOGIN_LOCKOUT_SECONDS` (900 s), login for that email returns `429` with `Retry-After`
until the window ends. A successful login clears the counter.

```bash
curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"admin@sentinel.example","password":"AgentOS!2026"}'
```

## Projects and runs

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/projects/templates` | auth | Project templates (`b2b_sales`, `competitive_research`, `document_analysis`, `generic`) with default objective, parameters and budget; demo support tickets; chaos keys |
| GET | `/projects` | auth | List projects with task counts, progress and pending approvals. Query: `status`, `limit` |
| POST | `/projects` | project.write | Create (and by default start) a project — body below. `201` |
| GET | `/projects/{id}` | auth | Project with objective, latest plan, tasks, approvals and metrics (tokens, cost, model calls, latency) |
| POST | `/projects/{id}/start` | project.write | Start a `DRAFT` project or restart a `FAILED` one. `409` otherwise |
| POST | `/projects/{id}/cancel` | project.write | Cancel a running project and its open tasks |
| GET | `/projects/{id}/graph` | auth | Task DAG for visualisation: `nodes`, `edges` (`dependency` or `delegation`) and `agents` |
| GET | `/projects/{id}/events` | auth | Stored events, oldest first. Query: `after` (event id), `limit` (≤ 5000) |
| GET | `/projects/{id}/replay` | auth | `{project, events, tasks}` — the ordered event log and final task set the console uses to scrub through a run |
| GET | `/projects/{id}/stream` | auth | Server-sent events for one project (see [Events](#events)) |
| POST | `/support/tickets` | project.write | Create a support ticket and start a support run: `{customer_email, subject, body, budget_usd=5, chaos={}}` → `{project_id, ticket_id}`. `201` |

`POST /projects` body:

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | string | required | 1–200 characters |
| `objective` | string | required | Natural-language goal the Supervisor plans from |
| `description` | string | `""` | |
| `template` | string | `generic` | One of the template keys |
| `parameters` | object | `{}` | Merged over the template's parameters (for example `target_count`, `top_n`, `region`) |
| `budget_usd` | number | template budget | `generic` projects default to the organisation's `default_project_budget_usd` |
| `chaos` | object | `{}` | Fault-injection rates 0–1 keyed by `provider_failure_rate`, `malformed_output_rate`, `invalid_response_rate`, `tool_failure_rate`, `tool_timeout_rate`, `worker_crash_rate` |
| `start` | boolean | `true` | `false` creates a `DRAFT` project |

```bash
curl -s localhost:8000/api/v1/projects -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"name":"UK device-control prospects","template":"b2b_sales",
       "objective":"Find 100 qualified UK companies ... prepare the best 20 prospects for approval.",
       "parameters":{"target_count":100,"top_n":20}}'
```

Project statuses: `DRAFT`, `PLANNING`, `RUNNING`, `WAITING_APPROVAL`, `COMPLETED`, `FAILED`,
`CANCELLED`. A completed project's `deliverable` holds the final output.

## Tasks and tool calls

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/tasks/{id}` | auth | `{task, agent, executions, steps, tool_calls, evaluations}` — the task, its agent, each execution attempt, runtime steps (THINK → … → COMPLETE), tool calls and QA evaluations |
| POST | `/tasks/{id}/retry` | project.write | Re-queue a `FAILED`, `ESCALATED`, `BLOCKED` or `CANCELLED` task and unblock its dependants. `409` otherwise |
| POST | `/tasks/{id}/cancel` | project.write | Cancel an unfinished task. `409` if already finished |
| POST | `/tasks/{id}/reassign` | project.write | Hand a task to a different agent: `{agent_key}`. `409` while `RUNNING` or once `COMPLETED` |
| GET | `/tool-calls` | auth | Tool call log. Query: `project_id`, `tool`, `status`, `limit` |

Task statuses: `PENDING`, `QUEUED`, `RUNNING`, `WAITING_APPROVAL`, `WAITING_CHILDREN`,
`IN_REVIEW`, `RETRY_SCHEDULED`, `BLOCKED`, `ESCALATED`, `COMPLETED`, `FAILED`, `CANCELLED`.

## Approvals

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/approvals` | auth | List approvals. Query: `status` (`PENDING`, `APPROVED`, `REJECTED`, `CHANGES_REQUESTED`, `EXPIRED`), `project_id`, `limit` |
| GET | `/approvals/summary` | auth | `{by_status, pending_by_risk}` |
| GET | `/approvals/{id}` | auth | Approval with payload, matched policies, risk, agent justification and decision |
| POST | `/approvals/{id}/decision` | approval.decide + required role | Decide — body below |

```json
{"decision": "edit", "comment": "Shorter subject line", "edited_payload": {"messages": [{"to": "…", "subject": "…", "body": "…"}]}}
```

`decision` is one of `approve`, `reject`, `edit` (requires `edited_payload`, validated against the
tool's input schema) or `request_changes` (requires `comment`; the task is returned to the agent
with the comment as feedback). Responses: `403` if the caller's role cannot decide this approval,
`409` if it has already been decided.

## Agents, tools and policies

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/agents` | auth | Agent definitions with usage statistics |
| GET | `/agents/{id}` | auth | One agent with recent tasks |
| POST | `/agents` | agent.write | Create an agent. `409` if `key` exists. `201` |
| PATCH | `/agents/{id}` | agent.write | Update any field (partial) |
| DELETE | `/agents/{id}` | agent.write | Delete a custom agent (`400` for built-in agents, which can only be disabled). `204` |
| GET | `/tools` | auth | Tool registry for the organisation: schema, permission level, category, approval flag, timeout, retry policy, usage |
| PATCH | `/tools/{id}` | tool.write | `{enabled, requires_approval, timeout_seconds, retry_policy, config}` (partial) |
| GET | `/policies` | auth | Policies, highest priority first |
| POST | `/policies` | policy.write | Create a policy. `201` |
| PUT | `/policies/{id}` | policy.write | Replace a policy |
| DELETE | `/policies/{id}` | policy.write | `204` |
| POST | `/policies/simulate` | auth | Evaluate policies against a hypothetical context: `{scope, context}` → effect, required role and matched policies |

Agent fields: `key`, `name`, `role`, `description`, `system_instructions`, `allowed_tools`,
`prohibited_tools`, `model_policy`, `max_iterations` (8), `token_budget` (60000),
`cost_budget_usd` (2.0), `memory_config`, `escalation_rules` (for example
`{"alternate_agent": "research"}`), `approval_rules`, `can_delegate_to`, `capabilities`, `color`,
`enabled`. See [AGENT_RUNTIME.md](AGENT_RUNTIME.md) for their meaning.

Policy body:

```json
{
  "name": "Refund above $1,000 requires finance manager",
  "scope": "tool_call",
  "condition": {"all": [{"field": "tool", "op": "eq", "value": "refund_issue"},
                        {"field": "facts.amount_usd", "op": "gt", "value": 1000}]},
  "effect": "require_approval",
  "required_role": "finance_manager",
  "priority": 100,
  "enabled": true
}
```

`scope` is `tool_call`, `knowledge_access` or `delegation`; `effect` is `deny`, `require_qa`,
`require_approval` or `allow`.

## Knowledge

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/knowledge/sources` | auth | Sources with chunk counts and classification |
| GET | `/knowledge/sources/{id}` | auth | Source with its chunks |
| POST | `/knowledge/sources` | knowledge.write | Multipart upload: `file`, `name`, `source_type` (`document`), `classification` (`public`/`internal`/`confidential`/`legal`), `tags` (comma-separated). Accepts `.pdf .docx .html .htm .csv .md .markdown .txt .json`, ≤ 20 MB. `201` |
| DELETE | `/knowledge/sources/{id}` | knowledge.write | Delete a source and its chunks. `204` |
| POST | `/knowledge/search` | auth | `{query, k=6, mode=hybrid\|semantic\|keyword, source_types, tags, source_ids}` → ranked passages with scores and citations |
| POST | `/knowledge/ask` | auth | `{question, …same filters}` → grounded answer with `[S1]`-style citations and the cited passages |

```bash
curl -s localhost:8000/api/v1/knowledge/sources -H "Authorization: Bearer $TOKEN" \
  -F file=@pricing.md -F classification=internal -F tags=sales,pricing
```

## Memory and entity graph

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/memory/items` | auth | Memory items. Query: `layer`, `trust_level`, `project_id`, `q` (semantic search), `limit` |
| GET | `/memory/summary` | auth | Counts by layer and trust level, entity and relationship counts |
| POST | `/memory/items` | memory.approve | Add human-approved organisational knowledge: `{content, kind=fact, key}`. `201` |
| POST | `/memory/items/{id}/approve` | memory.approve | Approve an item; `{promote: true}` copies it to long-term organisational memory. `409` for rejected items |
| POST | `/memory/items/{id}/reject` | memory.approve | Reject an item so it is never recalled |
| GET | `/memory/entities` | auth | Entities and relationships for the graph view. Query: `type`, `project_id`, `q`, `limit` |

## Costs and usage

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/costs/summary` | auth | Spend and tokens for the last `days` (30): totals, daily series, by model, provider, agent, purpose and project, monthly budget usage |
| GET | `/costs/usage` | auth | Recent model calls. Query: `project_id`, `limit` |

## Business data (demo integrations)

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/crm/accounts` | auth | CRM accounts. Query: `limit` |
| GET | `/crm/contacts` | auth | Query: `account_id`, `limit` |
| GET | `/crm/opportunities` | auth | Query: `project_id`, `limit` |
| GET | `/outbox` | auth | Messages delivered by the email and Slack adapters. Query: `project_id`, `channel`, `limit` |
| GET | `/tickets` | auth | Support tickets |
| GET | `/files` | auth | Files produced by agents. Query: `project_id` |
| GET | `/files/{id}/content` | auth | Download a file |

## Organisation, users and secrets

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/org` | auth | Organisation, settings and workspaces |
| PATCH | `/org` | settings.write | `{name, monthly_budget_usd, settings}` — settings below |
| POST | `/org/webhook-secret/rotate` | settings.write | Generate a new webhook signing secret; returned once |
| POST | `/org/workspaces` | settings.write | `{name, description}`. `201` |
| GET | `/users` | auth | Users in the organisation |
| POST | `/users` | user.write | `{email, name, role, title, password}`. `409` if the email exists. `201` |
| PATCH | `/users/{id}` | user.write | `{role, is_active, title}` |
| GET | `/secrets` | settings.write | Secret names with masked previews |
| PUT | `/secrets` | settings.write | Create or replace `{name, value, description}`; the value is encrypted and never returned |
| DELETE | `/secrets/{id}` | settings.write | `204` |
| GET | `/notifications` | auth | Notifications for the caller's role. Query: `unread` |
| POST | `/notifications/read-all` | auth | `204` |

Editable organisation settings (others are rejected with `422`):

| Key | Type | Effect |
|---|---|---|
| `quality_threshold` | 0–1 | Minimum QA score for every review; tasks may set a higher bar |
| `max_revisions` | 0–10 | Default revision rounds before a task is escalated |
| `default_project_budget_usd` | > 0 | Default budget for custom (`generic`) projects |
| `communication_rules` | object | Outreach guidance passed to writing agents and checked by QA/Compliance |

## Audit

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/audit` | audit.read | Newest-first audit records. Query: `action`, `actor_id`, `project_id`, `resource_type`, `before_seq` (paging), `limit` |
| GET | `/audit/verify` | audit.read | Recompute the hash chain: `{valid, records, head}` or `{valid: false, broken_at_seq}` |

## Events

`GET /projects/{id}/stream` (one project) and `GET /events/stream` (whole organisation) are
`text/event-stream` responses. On connect they replay up to the 200 most recent events, then
deliver new events as they are committed. Each message is:

```
event: event
id: <event id>
data: {"id": "…", "type": "TASK_COMPLETED", "project_id": "…", "task_id": "…", "agent_key": "research",
       "message": "Research Agent completed …", "payload": {…}, "created_at": "2026-10-11T16:04:02.113Z"}
```

An `event: ping` message is sent every 2 s when idle. Events are read from the database, so
a reconnecting client never misses committed events. `GET /events` returns recent events as
JSON (query: `type`, `limit`).

Event types: `PROJECT_CREATED`, `PROJECT_STARTED`, `PLAN_CREATED`, `TASK_CREATED`,
`TASK_ASSIGNED`, `TASK_QUEUED`, `AGENT_STARTED`, `AGENT_STEP`, `TOOL_CALLED`, `TOOL_COMPLETED`,
`TOOL_FAILED`, `TOOL_DENIED`, `MODEL_FALLBACK`, `MODEL_DOWNGRADED`, `TASK_DELEGATED`,
`TASK_REVIEW_STARTED`, `TASK_REVISION_REQUESTED`, `TASK_COMPLETED`, `TASK_FAILED`,
`TASK_RETRY_SCHEDULED`, `TASK_BLOCKED`, `TASK_CANCELLED`, `TASK_ESCALATED`, `TASK_REASSIGNED`,
`TASK_RECOVERED`, `APPROVAL_REQUIRED`, `APPROVAL_GRANTED`, `APPROVAL_REJECTED`,
`APPROVAL_CHANGES_REQUESTED`, `BUDGET_WARNING`, `BUDGET_EXCEEDED`, `INJECTION_DETECTED`,
`MEMORY_WRITTEN`, `PROJECT_COMPLETED`, `PROJECT_FAILED`, `PROJECT_CANCELLED`.

```js
const es = new EventSource(`/api/v1/projects/${id}/stream?access_token=${token}`);
es.addEventListener("event", (m) => console.log(JSON.parse(m.data)));
```

## Webhooks

`POST /webhooks/support/{org_slug}` (public, signed) — create a support ticket from an external
helpdesk and start a support run. Returns `202` with `{status: "accepted", ticket_id, project_id}`,
or `{status: "duplicate", …}` when `external_id` was already received.

Headers: `X-AgentOS-Timestamp` (Unix seconds, within 300 s) and
`X-AgentOS-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + raw body>` using the secret
from `POST /org/webhook-secret/rotate`.

```python
import hashlib, hmac, json, time, httpx

body = json.dumps({"external_id": "ZD-4411", "customer_email": "it@acme.example",
                   "subject": "Laptops blocked after update", "body": "…"}).encode()
ts = str(int(time.time()))
sig = "sha256=" + hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
httpx.post(f"{BASE}/api/v1/webhooks/support/sentinel", content=body,
           headers={"Content-Type": "application/json", "X-AgentOS-Timestamp": ts, "X-AgentOS-Signature": sig})
```

## System

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/healthz` | public | Liveness: `{"status": "ok"}` |
| GET | `/readyz` | public | Readiness: `{status, checks}` for the database, the job queue and start-up (schema and seeding); `503` until all pass |
| GET | `/metrics` | public | Prometheus metrics (see [OPERATIONS.md](OPERATIONS.md#observability)) |
| GET | `/api/v1/system/status` | auth | Environment, database and queue backends, queue depth, embedded worker pool, model providers with circuit-breaker state, provider order, model catalogue, purpose → tier mapping, runtime limits and task counts |
| POST | `/api/v1/system/recover` | settings.write | Run the platform-wide crash-recovery sweep now (it also runs automatically every `AGENTOS_RECOVERY_INTERVAL_SECONDS`): re-queue running tasks whose lease expired, due retries, queued tasks whose job was lost, and projects stuck in planning. Returns counts per category |
