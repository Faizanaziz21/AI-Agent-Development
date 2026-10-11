# AgentOS — Architecture (as built)

AgentOS turns a business objective into a plan of tasks, assigns them to specialised agents,
executes them with governed tools, checks their output, pauses for humans where policy requires,
and keeps an auditable record of everything. This document describes how the code is put
together. [`AGENT_RUNTIME.md`](AGENT_RUNTIME.md) covers the agent loop, model gateway and memory in
depth; [`SECURITY.md`](SECURITY.md) covers the threat model.

## 1. Components

```mermaid
flowchart LR
  subgraph Clients
    UI[React console]
    HD[Helpdesk / email gateway]
  end
  subgraph API["API tier (stateless, N replicas)"]
    GW[FastAPI routes<br/>auth · RBAC · rate limit]
    ORCH[Orchestrator<br/>DAG scheduling · state machine]
    PLAN[Planner]
    APPR[Approval service]
    RAG[Knowledge / RAG]
    SSE[SSE streams]
  end
  subgraph Workers["Worker tier (N replicas × concurrency)"]
    WP[Worker pool<br/>leases · checkpoints · recovery]
    RT[Agent runtime]
    TX[Tool executor]
    POL[Policy engine]
    MG[Model gateway]
    MEM[Memory service]
  end
  subgraph Data
    PG[(PostgreSQL)]
    RD[(Redis<br/>queue · pub/sub · rate limits)]
    OBJ[(Object storage<br/>S3 or shared volume)]
  end
  subgraph Providers
    OAI[OpenAI]
    ANT[Anthropic]
    LOC[Local reasoning engine]
  end

  UI -- REST --> GW
  UI -- EventSource --> SSE
  HD -- HMAC-signed webhook --> GW
  GW --> ORCH & APPR & RAG
  ORCH --> PLAN
  ORCH -- enqueue --> RD
  RD -- dequeue --> WP
  WP --> RT
  RT --> MG & TX & MEM
  TX --> POL
  TX -- sensitive action --> APPR
  MG --> OAI & ANT & LOC
  ORCH & RT & TX -. events .-> RD
  RD -. pub/sub .-> SSE
  API & Workers --> PG
  RAG & TX --> OBJ
```

| Package | Responsibility |
|---|---|
| `app/api/routes/` | REST surface: auth, org, agents/tools/policies, knowledge, projects/tasks, approvals, memory, costs, audit, business data, webhooks, system |
| `app/services/orchestrator.py` | Owns every task state transition: scheduling dependents, delegation, QA reviews, approvals, retries, escalation, budget pauses, recovery, cancellation |
| `app/services/planning/` | Objective → validated task DAG (`dag.py` checks acyclicity, agent/capability existence, size limits) |
| `app/services/worker.py` | Worker pool: dequeues jobs, takes a database lease on the task, runs the runtime, renews/expires leases, periodic recovery sweep |
| `app/services/agents/` | Agent profile (from DB), context builder, decision parser with repair, runtime loop |
| `app/services/tools/` | Tool base class + registry, 26 built-in tools, the executor pipeline |
| `app/services/policy/` | Condition DSL and decision engine over the `policies` table |
| `app/services/model_gateway/` | Model catalogue, router, providers, circuit breakers, fallback, fault injection, usage metering; local reasoning engine |
| `app/services/rag/` | Parsing (PDF, DOCX, HTML, CSV, Markdown, JSON), chunking, embeddings, hybrid retrieval with reciprocal-rank fusion |
| `app/services/memory.py` | Session and long-term memory, trust levels, promotion, semantic recall |
| `app/services/budget.py` | Budget state across organisation / project / agent / task scopes |
| `app/services/approvals.py` | Decision validation (RBAC, edit schema validation), resume semantics |
| `app/services/security/injection.py` | Sanitising, scanning, neutralising and fencing untrusted content |
| `app/services/audit.py` | Hash-chained append-only audit log and verification |
| `app/core/` | Settings, database engine/sessions, JWT + RBAC, crypto, event bus/outbox, telemetry |

One image runs as the API (`uvicorn app.main:app`) or as a worker (`python -m app.worker`). For
local development the API can also host an embedded worker pool (`AGENTOS_EMBEDDED_WORKERS`), so
`uvicorn` alone runs the whole platform on SQLite with no Redis.

## 2. Project lifecycle

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant API
  participant O as Orchestrator
  participant Q as Queue
  participant W as Worker
  participant S as Supervisor agent
  participant A as Specialist agents
  participant QA as QA agent
  participant H as Human approver

  U->>API: POST /projects (objective, template, budget)
  API->>O: start_project
  O->>Q: enqueue planning job
  W->>S: plan(objective)
  S-->>W: task DAG (agents, capabilities, dependencies, review criteria)
  W->>O: persist Plan + Tasks, schedule roots
  loop until every task is terminal
    O->>Q: enqueue ready tasks (dependencies completed)
    W->>A: run task (lease + checkpoints)
    alt output requires review
      O->>Q: enqueue QA review task
      W->>QA: evaluate against criteria
      QA-->>O: score + feedback
      alt score below threshold and revisions left
        O->>Q: re-queue task with feedback (revision n+1)
      else below threshold, no revisions left
        O->>H: quality escalation approval
      end
    end
    opt sensitive tool call or budget exhausted
      A->>H: approval request (task WAITING_APPROVAL)
      H-->>O: approve / edit / reject / request changes
      O->>Q: resume task from checkpoint
    end
  end
  O->>API: PROJECT_COMPLETED + deliverable
  API-->>U: live events over SSE throughout
```

## 3. Task state machine

```mermaid
stateDiagram-v2
  [*] --> PENDING
  PENDING --> QUEUED: dependencies completed
  PENDING --> BLOCKED: a dependency failed or was cancelled
  BLOCKED --> QUEUED: dependency retried and completed
  QUEUED --> RUNNING: worker takes lease
  RUNNING --> WAITING_APPROVAL: sensitive tool / budget limit
  WAITING_APPROVAL --> QUEUED: approved, edited, rejected or changes requested
  RUNNING --> WAITING_CHILDREN: delegated subtasks
  WAITING_CHILDREN --> QUEUED: all children terminal
  RUNNING --> IN_REVIEW: output needs QA
  IN_REVIEW --> COMPLETED: score ≥ threshold
  IN_REVIEW --> QUEUED: revision requested
  IN_REVIEW --> ESCALATED: revisions exhausted
  RUNNING --> RETRY_SCHEDULED: transient failure
  RETRY_SCHEDULED --> QUEUED: backoff elapsed
  RUNNING --> QUEUED: reassigned to alternate agent
  RUNNING --> ESCALATED: unrecoverable, loop detected, retries exhausted
  ESCALATED --> QUEUED: human approves retry / reassigns
  ESCALATED --> FAILED: human rejects
  RUNNING --> COMPLETED
  RUNNING --> FAILED
  PENDING --> CANCELLED
  QUEUED --> CANCELLED
  RUNNING --> CANCELLED
  COMPLETED --> [*]
  FAILED --> [*]
  CANCELLED --> [*]
```

A rejected approval does not fail the task outright: the agent resumes, observes the rejection
as a tool result, and decides what to do next (for example, finish without sending). Only the
orchestrator writes task status; the runtime returns a `RunResult` and the orchestrator applies
the transition in one transaction together with its events.

## 4. Data model

```mermaid
erDiagram
  ORGANIZATION ||--o{ WORKSPACE : has
  ORGANIZATION ||--o{ USER : has
  ORGANIZATION ||--o{ AGENT_DEFINITION : defines
  ORGANIZATION ||--o{ TOOL_DEFINITION : configures
  ORGANIZATION ||--o{ POLICY : enforces
  ORGANIZATION ||--o{ SECRET : stores
  WORKSPACE ||--o{ PROJECT : contains
  PROJECT ||--|| OBJECTIVE : pursues
  OBJECTIVE ||--o{ PLAN : "planned by (versioned)"
  PLAN ||--o{ TASK : contains
  TASK ||--o{ TASK : "parent of (delegation, review)"
  TASK ||--o{ EXECUTION : "attempts"
  EXECUTION ||--o{ EXECUTION_STEP : records
  TASK ||--o{ TOOL_CALL : makes
  TASK ||--o{ EVALUATION : "scored by"
  TASK ||--o{ TASK_REVISION : "revised as"
  TASK ||--o{ APPROVAL : requests
  TASK ||--o{ MODEL_USAGE : incurs
  PROJECT ||--o{ MEMORY_ITEM : remembers
  PROJECT ||--o{ ENTITY : extracts
  ENTITY ||--o{ RELATIONSHIP : links
  ORGANIZATION ||--o{ KNOWLEDGE_SOURCE : uploads
  KNOWLEDGE_SOURCE ||--o{ KNOWLEDGE_CHUNK : "chunked into"
  ORGANIZATION ||--o{ EVENT : emits
  ORGANIZATION ||--o{ AUDIT_LOG : records
  ORGANIZATION ||--o{ NOTIFICATION : sends
  ORGANIZATION ||--o{ OUTBOUND_MESSAGE : "queues (email, Slack)"
  ORGANIZATION ||--o{ SUPPORT_TICKET : receives
```

34 tables in total (`migrations/versions/20261011_0001_initial_schema.py`), including the demo
CRM (`crm_accounts`, `crm_contacts`, `crm_opportunities`) and generated files. Every
tenant-owned table carries an indexed `org_id`; route handlers load rows through
`get_owned()`, which returns 404 for rows of another organisation, and list queries filter on
the caller's `org_id`. Tests attempt cross-tenant access on every resource type.

## 5. Events, outbox and live streams

Every state change emits an `EventRecord` in the same transaction as the change (a transactional
outbox). After commit the event bus publishes it, in-process and on Redis pub/sub when
configured, so any API replica can stream events produced by any worker. Clients subscribe with
`EventSource` to `/api/v1/projects/{id}/stream` or `/api/v1/events/stream` (the bearer token is
passed as `access_token` because `EventSource` cannot set headers). Because events are persisted,
the console's **Replay** tab can rebuild the full state of a past project at any point in time.

Event types: project (`CREATED`, `STARTED`, `COMPLETED`, `FAILED`, `CANCELLED`), `PLAN_CREATED`,
task (`CREATED`, `ASSIGNED`, `QUEUED`, `DELEGATED`, `REVIEW_STARTED`, `REVISION_REQUESTED`,
`RETRY_SCHEDULED`, `BLOCKED`, `ESCALATED`, `REASSIGNED`, `RECOVERED`, `COMPLETED`, `FAILED`,
`CANCELLED`), `AGENT_STARTED`, `AGENT_STEP`, tool (`CALLED`, `COMPLETED`, `FAILED`, `DENIED`),
model (`FALLBACK`, `DOWNGRADED`), approval (`REQUIRED`, `GRANTED`, `REJECTED`,
`CHANGES_REQUESTED`), budget (`WARNING`, `EXCEEDED`), `INJECTION_DETECTED`, `MEMORY_WRITTEN`.

## 6. Queue, leases and crash recovery

```mermaid
sequenceDiagram
  participant Q as Queue (Redis list / in-memory heap)
  participant W1 as Worker 1
  participant DB as PostgreSQL
  participant W2 as Worker 2
  W1->>Q: BLMOVE ready → processing:W1 (reliable queue)
  W1->>DB: UPDATE task SET status=RUNNING, lease_owner=W1, lease_expires_at=now+120s WHERE status=QUEUED
  loop every step
    W1->>DB: checkpoint (iteration, messages, tool results) + renew lease
  end
  Note over W1: process killed
  W2->>DB: recovery sweep: RUNNING with expired lease
  W2->>DB: status=QUEUED, attempt+1, keep checkpoint (TASK_RECOVERED)
  W2->>Q: enqueue
  W2->>DB: take lease, resume from checkpoint (completed tool calls are not repeated)
```

- Jobs carry only a task id. The database lease is the source of truth, so a duplicate
  delivery is harmless: the second worker fails the conditional update and drops the job.
- With Redis, a dequeued job moves atomically into a per-worker processing list and is removed
  only after the task attempt finishes; jobs held by dead workers are moved back to the ready
  list. Delayed jobs (retry backoff) wait in a sorted set until due.
- Each worker runs a recovery sweep at start-up and every `AGENTOS_RECOVERY_INTERVAL_SECONDS`.
  It re-queues tasks whose lease expired, tasks stuck in `QUEUED` whose job was lost, elapsed
  `RETRY_SCHEDULED` tasks, and projects stuck in `PLANNING`.
- Transient failures retry with exponential backoff (1 s, 2 s, 4 s … capped at 60 s) up to
  `max_attempts`, then reassign to the agent's `escalation_rules.alternate_agent` if set, and
  otherwise escalate to a human.

## 7. Deployment topology

```mermaid
flowchart TB
  LB[Ingress / load balancer<br/>TLS] --> WEB[web: nginx + SPA<br/>2+ replicas]
  WEB -- /api --> API[api: FastAPI<br/>HPA 2–10]
  API --> PG[(Managed PostgreSQL)]
  API --> RD[(Managed Redis)]
  WK[worker: python -m app.worker<br/>HPA / KEDA 2–30 × 8 tasks] --> PG
  WK --> RD
  WK --> EXT[Model providers & external APIs]
  API & WK --> OBJ[(S3 / RWX volume)]
  PROM[Prometheus] -. scrape /metrics .-> API & WK
  OTEL[OTel collector / Jaeger] -. OTLP traces .- API & WK
  MIG[migrate Job: alembic upgrade head] --> PG
```

See [`OPERATIONS.md`](OPERATIONS.md) for Compose and Kubernetes instructions, configuration and
runbooks.
