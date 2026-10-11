# AgentOS — Operations Guide

How to run, configure, observe, scale and troubleshoot AgentOS. For the component design see
[ARCHITECTURE.md](ARCHITECTURE.md); for security controls see [SECURITY.md](SECURITY.md).

## 1. Deployment modes

| Mode | Use | Database / queue | Workers |
|---|---|---|---|
| Local development | Hacking on the code | SQLite + in-memory queue | Embedded in the API process (`AGENTOS_EMBEDDED_WORKERS=4`) |
| Docker Compose | Full stack on one machine, demos | PostgreSQL 16 + Redis 7 | Separate `worker` service (2 replicas × 8 concurrency) |
| Kubernetes | Production | Managed PostgreSQL + Redis | `agentos-worker` Deployment with HPA (or KEDA) |

The same image runs the API (`uvicorn app.main:app`) and workers (`python -m app.worker`). API
replicas are stateless; workers coordinate only through the database (task leases) and Redis
(job queue and event fan-out), so either can be scaled independently.

### 1.1 Local development

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,otlp]"
.venv/bin/uvicorn app.main:app --reload --port 8000     # creates ./data/agentos.db and seeds demo data

cd ../frontend
npm ci && npm run dev                                   # http://localhost:5173, proxies /api to :8000
```

Sign in as `admin@sentinel.example` / `AgentOS!2026` (other demo users are listed in
[DEMOS.md](DEMOS.md)). Without provider API keys the built-in local model engine is used, so the
whole platform works offline.

### 1.2 Docker Compose

```bash
docker compose up -d --build
# optional: OPENAI_API_KEY=... ANTHROPIC_API_KEY=... docker compose up -d
```

| Service | Purpose | URL |
|---|---|---|
| `web` | Console (nginx, proxies `/api`) | http://localhost:8080 |
| `api` | API (`AGENTOS_EMBEDDED_WORKERS=0`) | http://localhost:8000/docs |
| `worker` ×2 | Task execution, metrics on :9100 | — |
| `migrate` | `alembic upgrade head`, runs once before `api` starts | — |
| `postgres`, `redis` | State and queue | localhost:5432 |
| `prometheus` | Metrics and alert rules | http://localhost:9090 |
| `grafana` | "AgentOS Overview" dashboard (anonymous viewer; admin/admin) | http://localhost:3000 |
| `jaeger` | Traces from `agentos-api` and `agentos-worker` | http://localhost:16686 |

Host ports for the web, API and PostgreSQL can be changed with `AGENTOS_WEB_PORT`,
`AGENTOS_API_PORT` and `AGENTOS_POSTGRES_PORT`. Compose runs with `AGENTOS_ENV=production`
and ships demo values for `AGENTOS_JWT_SECRET` and `AGENTOS_SECRETS_KEY`; override both before
storing real data.

Verify a running stack end-to-end with the black-box smoke test, which signs in, creates a
project through the public API, approves every approval it raises and waits for completion:

```bash
python backend/scripts/http_smoke.py --base http://localhost:8080 --template b2b_sales
python backend/scripts/http_smoke.py --base http://localhost:8080 --template support
```

It exits non-zero unless the project reaches `COMPLETED`.

### 1.3 Kubernetes

Manifests are in `deploy/k8s` and assume managed PostgreSQL and Redis.

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl -n agentos create secret generic agentos-secrets \
  --from-literal=AGENTOS_DATABASE_URL='postgresql+asyncpg://…' \
  --from-literal=AGENTOS_REDIS_URL='rediss://…' \
  --from-literal=AGENTOS_JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=AGENTOS_SECRETS_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
kubectl -n agentos apply -f deploy/k8s/configmap.yaml

# every release: migrate first (Jobs are immutable, so replace it)
kubectl -n agentos delete job agentos-migrate --ignore-not-found
kubectl -n agentos apply -f deploy/k8s/migrate-job.yaml
kubectl -n agentos wait --for=condition=complete job/agentos-migrate --timeout=5m

kubectl apply -k deploy/k8s
```

| Object | Notes |
|---|---|
| `agentos-api` Deployment | 2–10 replicas (HPA at 70% CPU), zero-downtime rolling updates, PDB `minAvailable: 1`, readiness on `/readyz`, liveness and startup on `/healthz` |
| `agentos-worker` Deployment | 2–30 replicas (HPA at 60% CPU), PDB `maxUnavailable: 1`, 60 s termination grace, liveness on the metrics port |
| `agentos-web` Deployment | nginx serving the console and proxying `/api` |
| Ingress | TLS via cert-manager; SSE-friendly (`proxy-buffering: off`, 1 h read timeout); 25 MB body limit |
| `agentos-objects` PVC | ReadWriteMany volume for uploads and generated files. Prefer S3 (`AGENTOS_S3_BUCKET`) in the cloud |
| NetworkPolicies | API reachable only from the web tier and the `monitoring` namespace; workers accept only metrics scrapes |
| `optional/keda-scaledobject.yaml` | Scale workers on Redis ready-queue length (remove the worker HPA first) |
| `optional/servicemonitor.yaml` | Prometheus Operator scrape configuration |

Set image tags with `kustomize edit set image` (CI publishes `ghcr.io/<owner>/agentos-backend`
and `agentos-frontend` on pushes to the main branch).

## 2. Configuration reference

All settings are environment variables with the `AGENTOS_` prefix (also read from a `.env` file).

### Core

| Variable | Default | Description |
|---|---|---|
| `AGENTOS_ENV` | `development` | `production` enforces a non-default JWT secret and an explicit secrets key |
| `AGENTOS_DATABASE_URL` | `sqlite+aiosqlite:///./data/agentos.db` | Use `postgresql+asyncpg://…` outside development |
| `AGENTOS_REDIS_URL` | — | Enables the Redis job queue, cross-process events, shared rate limits and sign-in throttling. Required for more than one process |
| `AGENTOS_AUTO_CREATE_SCHEMA` | `true` | Create tables at start-up. Set `false` and run `alembic upgrade head` in production |
| `AGENTOS_SEED_DEMO` | `true` | Seed demo organisations, users, agents, tools, policies, knowledge and CRM data (idempotent) |
| `AGENTOS_STATIC_DIR` | — | Serve a built console from the API process |
| `AGENTOS_LOG_LEVEL` | `INFO` | Log level for stdout logs |
| `AGENTOS_CORS_ORIGINS` | `http://localhost:5173,http://localhost:8080` | Comma-separated |

### Security

| Variable | Default | Description |
|---|---|---|
| `AGENTOS_JWT_SECRET` | dev value | HS256 signing key; must be changed in production |
| `AGENTOS_JWT_TTL_MINUTES` | `720` | Access token lifetime |
| `AGENTOS_SECRETS_KEY` | derived in dev | Fernet key for organisation secrets and webhook secrets |
| `AGENTOS_RATE_LIMIT_PER_MINUTE` | `600` | Per user (or IP) per minute |
| `AGENTOS_LOGIN_MAX_FAILURES` | `10` | Failed sign-ins per email before lockout |
| `AGENTOS_LOGIN_LOCKOUT_SECONDS` | `900` | Failure counting window and lockout duration |

### Workers and orchestration

| Variable | Default | Description |
|---|---|---|
| `AGENTOS_EMBEDDED_WORKERS` | `4` | Worker concurrency inside the API process; `0` when running separate workers |
| `AGENTOS_WORKER_CONCURRENCY` | `8` | Concurrent tasks per `app.worker` process |
| `AGENTOS_WORKER_METRICS_PORT` | `9100` | Prometheus endpoint of `app.worker` |
| `AGENTOS_WORKER_LEASE_SECONDS` | `120` | Task lease length, renewed at every checkpoint. A crashed worker's tasks are recovered after it expires |
| `AGENTOS_RECOVERY_INTERVAL_SECONDS` | `15` | How often each worker runs the recovery sweep |
| `AGENTOS_MAX_DELEGATION_DEPTH` | `3` | Maximum nesting of delegated sub-tasks |
| `AGENTOS_MAX_CHILDREN_PER_TASK` | `8` | Maximum sub-tasks one task may delegate |
| `AGENTOS_MAX_TASKS_PER_PROJECT` | `200` | Hard cap on tasks in a project |
| `AGENTOS_DEFAULT_QUALITY_THRESHOLD` | `0.75` | QA pass score when a plan does not set one (organisations can raise it) |
| `AGENTOS_MAX_REVISIONS` | `2` | QA revision rounds before escalation (organisations can override) |

### Models and retrieval

| Variable | Default | Description |
|---|---|---|
| `AGENTOS_OPENAI_API_KEY` | — | Enables the OpenAI provider |
| `AGENTOS_ANTHROPIC_API_KEY` | — | Enables the Anthropic provider |
| `AGENTOS_PROVIDER_ORDER` | `openai,anthropic,local` | Preference order; providers without credentials are skipped. `local-backup` is always appended as the last resort |
| `AGENTOS_LOCAL_MODEL_LATENCY_MS` | `120` | Simulated latency of the built-in local engine |
| `AGENTOS_EMBEDDING_PROVIDER` | `local` | `openai` uses OpenAI embeddings when a key is set; otherwise a local hashing embedder |
| `AGENTOS_OTEL_EXPORTER_ENDPOINT` | — | OTLP/HTTP traces endpoint, e.g. `http://jaeger:4318/v1/traces` |

### Integrations (no prefix)

| Variable | Effect when set (otherwise a local/outbox implementation is used) |
|---|---|
| `SERPER_API_KEY` | Live web search |
| `COMPANIES_HOUSE_API_KEY` | UK company registry lookups |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Real email delivery (otherwise emails go to the outbox shown in the console) |
| `SLACK_WEBHOOK_URL` | Real Slack delivery |
| `AGENTOS_S3_BUCKET` (+ standard AWS credentials) | S3 object storage instead of `AGENTOS_OBJECT_STORAGE_PATH` (`./data/objects`) |

## 3. Database migrations

Alembic migrations live in `backend/migrations`.

```bash
cd backend
AGENTOS_DATABASE_URL=postgresql+asyncpg://… .venv/bin/alembic upgrade head
.venv/bin/alembic check          # fails if models and migrations have drifted
.venv/bin/alembic downgrade -1
```

CI runs `upgrade head`, `check` and a full downgrade against PostgreSQL on every push. Run
migrations before rolling out new API or worker pods. During a rolling update old and new pods
run side by side, so schema changes should be additive (add, backfill, then remove in a later
release).

## 4. Observability

### 4.1 Metrics

The API exposes `/metrics`; each worker exposes `:9100/metrics`.

| Metric | Type | Labels |
|---|---|---|
| `agentos_http_requests_total` / `agentos_http_request_seconds` | counter / histogram | method, route, status |
| `agentos_tasks_total` | counter | status, agent |
| `agentos_task_duration_seconds` | histogram | agent |
| `agentos_tool_calls_total` / `agentos_tool_seconds` | counter / histogram | tool, status |
| `agentos_model_calls_total` / `agentos_model_seconds` | counter / histogram | provider, model, status |
| `agentos_model_tokens_total` | counter | provider, model, direction |
| `agentos_model_cost_usd_total` | counter | provider, model |
| `agentos_queue_depth` | gauge | — |
| `agentos_active_workers` | gauge | — |
| `agentos_approvals_pending` | gauge | — |
| `agentos_policy_decisions_total` | counter | effect |
| `agentos_prompt_injection_flags_total` | counter | source |
| `agentos_recoveries_total` | counter | kind (`expired_leases`, `retries_due`, `requeued`, `planning`, `approvals_resumed`) |

The provisioned Grafana dashboard (`deploy/grafana/dashboards/agentos-overview.json`) shows
API request rate and latency, task transitions and duration by agent, queue depth and busy
workers, model calls by provider with latency, tokens and spend, tool outcomes and latency,
policy decisions, pending approvals, injection flags and recoveries.

### 4.2 Alerts

`deploy/prometheus/alerts.yml`:

| Alert | Condition |
|---|---|
| `AgentOSApiDown` | API target down for 2 m |
| `AgentOSNoWorkers` | No healthy worker targets for 5 m |
| `AgentOSQueueBacklog` | Queue depth > 200 for 10 m |
| `AgentOSHighApiErrorRate` | 5xx ratio > 5% for 5 m |
| `AgentOSModelFailures` | A provider's error ratio > 25% for 10 m |
| `AgentOSTaskFailures` | > 0.1 failed tasks/s over 15 m |
| `AgentOSApprovalsPiling` | > 25 pending approvals for 30 m |
| `AgentOSInjectionSpike` | > 20 injection flags in 1 h |
| `AgentOSSpendRate` | > $50 model spend in 1 h |

### 4.3 Traces and logs

With `AGENTOS_OTEL_EXPORTER_ENDPOINT` set, the API and workers export OpenTelemetry traces
(service names `agentos-api` and `agentos-worker`) with spans for planning (`plan.create`), task
runs (`task.run`), model calls (`model.complete`) and tool calls (`tool.execute`). Every API
response carries an `X-Request-ID` header (taken from the request when supplied) for correlating
client reports with server logs.

Inside the product, every run is fully inspectable without external tooling: the project page
shows the live task graph, timeline and per-task steps, tool calls, model calls, QA reviews and
approvals, and the replay view scrubs through a finished run.

## 5. Capacity and scaling

### 5.1 Load test

`backend/scripts/load_test.py` drives many concurrent projects through the real orchestrator,
worker pool, policy engine and model gateway (local engine), with a simulated reviewer approving
every approval after 0.5 s and optional fault injection.

```bash
cd backend
.venv/bin/python scripts/load_test.py --projects 40 --workers 12 --mix support:3,b2b_sales:1 --chaos 0.1 --out load-report.json
```

Reference run (40 projects — 30 support tickets and 10 B2B sales pipelines — 12 worker slots,
10% provider-failure chaos plus tool failures, malformed output and simulated worker crashes;
SQLite, single process, 4-vCPU VM):

| Measure | Result |
|---|---|
| Projects completed | 40 / 40 in 60 s (≈ 40 projects/min) |
| Project latency | p50 45 s · p95 58 s · p99 58 s |
| Tasks | 480 completed; 10 revised after QA; 1 retried |
| Task latency | p50 0.85 s · p95 3.7 s |
| Task throughput | ≈ 8 tasks/s |
| Queue depth | max 79 · mean 30 |
| Model calls | 1,449 (130 failed, 129 recovered by fallback to the backup provider) |
| Tokens / simulated cost | 13.8 M / $22.69 |
| Tool calls | 713 succeeded; 41 paused for approval and resumed after the decision |
| Checkpoint resumptions | 80 (after approvals, retries and simulated worker crashes) |

The full report is in [`docs/load-report.json`](load-report.json). Every injected fault was absorbed by fallback, retry or checkpoint recovery; no project failed or
stalled. Project latency is dominated by the queue (all 40 projects are submitted at once) and by
the simulated reviewer, not by task execution. CI runs a smaller version
(`--projects 6 --workers 6 --chaos 0.05`) on every push.

### 5.2 Scaling guidance

- **Workers** are the main scaling axis. Each task holds a worker slot only while it is executing;
  tasks waiting for approval, children or retries hold nothing. Scale on queue depth
  (`agentos_queue_depth`, or KEDA on the Redis list) rather than CPU when using hosted models,
  because workers mostly wait on provider I/O. Raising `AGENTOS_WORKER_CONCURRENCY` is cheaper
  than adding pods until the database connection pool becomes the limit.
- **API** replicas scale with console users and SSE connections; each open stream polls the
  events table every 2 s when idle.
- **PostgreSQL** carries leases, checkpoints, events and the audit log. Budget roughly
  `(api replicas + worker replicas) × pool size` connections and front it with PgBouncer
  (transaction mode) past a few hundred. Archive `events`, `execution_steps` and `model_usage`
  partitions older than your retention period.
- **Redis** holds only queue and pub/sub state; enable AOF persistence (Compose does) so queued
  jobs survive a restart. Even without it, the recovery sweep re-queues tasks whose jobs were lost.
- **Model providers** are usually the real bottleneck: provider rate limits trip the circuit
  breaker and shift traffic to the next provider. Configure at least two providers for
  production.

## 6. Reliability behaviour

| Failure | What happens |
|---|---|
| Worker crash or pod eviction | Its task leases stop being renewed. After `AGENTOS_WORKER_LEASE_SECONDS` the recovery sweep returns the tasks to `QUEUED` and another worker resumes them from their last checkpoint (completed tool calls are not repeated) |
| Worker shutdown (SIGTERM) | In-flight tasks are cancelled and recovered as above once their lease expires |
| API restart | No impact on running work; SSE clients reconnect and replay from the database |
| Redis restart | Queued jobs survive with AOF. Otherwise the recovery sweep re-enqueues `QUEUED` tasks when the queue is empty |
| Model provider outage | Fallback down the provider order; a provider's circuit breaker opens after 3 consecutive failures and lets a probe through after 20 s |
| Malformed model output | The step is retried with a repair instruction; repeated malformed output fails the attempt, which then follows the task's retry policy |
| Tool timeout or transient error | Retried with exponential backoff and jitter per the tool's retry policy |
| Task exhausts retries | Reassigned to the agent's `escalation_rules.alternate_agent` if set; otherwise escalated to a human approval (retry, reassign or fail) |
| Budget reached | The task pauses and requests a budget-extension approval; models are downgraded to cheaper tiers from 80% of budget |
| Reviewer decides before the task has parked | The task resumes immediately when it parks; the recovery sweep re-applies any decision that was still missed |

## 7. Runbooks

**A project looks stuck.** Open it in the console. Check its graph for tasks in
`WAITING_APPROVAL` (decide them on the Approvals page), `ESCALATED` (retry, reassign or cancel
from the task drawer) or `BLOCKED` (an upstream task failed; retry it and its dependants
unblock). If tasks are `QUEUED` but nothing runs, check that workers are up
(`AgentOSNoWorkers`) and that `/readyz` reports the queue as `ok`. `POST /api/v1/system/recover`
(Settings → System → "Run crash recovery now") forces a recovery sweep immediately.

**Queue backlog alert.** Compare `agentos_active_workers` with total worker concurrency. If all
slots are busy, scale workers. If slots are idle while the queue grows, workers cannot reach
Redis or the database — check worker logs and `/readyz`.

**Model failures alert.** Settings → System shows each provider's circuit state. Confirm the
provider's status page and API key; traffic continues on the remaining providers. Add another
provider to `AGENTOS_PROVIDER_ORDER` if only one is configured.

**Spend alert.** The Costs page breaks spend down by project, agent, model and purpose. Lower
project budgets, the organisation's monthly budget, or move agents to cheaper tiers via their
`model_policy`.

**Injection spike.** Filter the project timeline or `GET /api/v1/events?type=INJECTION_DETECTED`
to find the source. Content was already neutralised, but review the source and consider removing
or reclassifying the knowledge document or restricting the tool's allowlist.

**Audit chain reports broken.** Treat as a security incident: the record at `broken_at_seq` or
the one before it was modified or deleted outside the application. Restore the audit table from
backup into a separate database and diff it against production.

**Locked-out user.** Failed sign-ins lock an email for `AGENTOS_LOGIN_LOCKOUT_SECONDS`. Wait it
out, or with Redis delete the key `agentos:fail:<email>`; without Redis, restarting the API clears
the counters.

**Rotating secrets.** Webhook secret: Settings → Organisation → "Rotate secret", then update the sender.
JWT secret: changing it signs every user out. Secrets key: re-encrypt stored secrets before
switching (decrypt with the old key, encrypt with the new one) — stored secrets cannot be read
after a key change otherwise.

## 8. Backups and data retention

- Back up PostgreSQL with point-in-time recovery; it holds all durable state.
- Back up the object store (uploads and generated files). Knowledge chunks and embeddings are in
  the database and can be rebuilt by re-uploading sources.
- Redis does not need backing up.
- Events, execution steps, model usage and tool calls grow with activity. Keep them for as long
  as you need run replays and cost history, then archive. Keep the audit log for your compliance
  period, together with its hash chain head.
