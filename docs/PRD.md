# AgentOS — Product Requirements Document

## 1. Problem

Enterprises want to delegate multi-step knowledge work (lead generation, support
resolution, research, document processing) to AI. Single-prompt chatbots fail at
this because real business work requires:

- decomposition into dependent steps,
- access to business systems through controlled, audited tools,
- verification of intermediate results,
- human sign-off for consequential actions,
- predictable cost,
- full auditability for compliance teams.

## 2. Product vision

AgentOS is a **digital workforce platform**: an organization configures
specialised AI "employees" (agents), gives them governed access to tools and
knowledge, and assigns them business objectives. AgentOS plans the work, runs
agents in parallel on distributed workers, verifies their output with evaluator
agents, routes sensitive actions to humans, enforces company policy and budgets,
and records every decision for replay and audit.

## 3. Personas

| Persona | Goals |
|---|---|
| **Business operator** (sales ops, support lead) | Submit objectives, watch progress, approve outputs |
| **Approver** (finance manager, compliance officer) | Review sensitive actions with full context, approve/reject/edit |
| **Platform admin** | Configure agents, tools, policies, models, budgets, secrets, users |
| **Auditor** | Replay any run, verify the audit chain, export evidence |
| **Developer** | Add tools/agents/providers through stable interfaces |

## 4. Functional requirements

### 4.1 Objectives and planning
- FR-1 A user submits a natural-language objective plus structured parameters into a project.
- FR-2 The Supervisor agent produces a structured plan: a DAG of tasks with assigned agent types, dependencies, expected outputs and review requirements.
- FR-3 Plans are validated (acyclic, known agents, permitted tools, task cap) before execution.
- FR-4 Tasks support sequential and parallel execution, blocking, retry, reassignment and cancellation.

### 4.2 Agents
- FR-5 Agents are database-defined: role, instructions, allowed/prohibited tools, model policy, iteration/token/cost budgets, memory config, escalation and approval rules.
- FR-6 Admins can create new agent types through UI/API without code changes.
- FR-7 Agents execute an explicit lifecycle (THINK → PLAN → SELECT_TOOL → EXECUTE_TOOL → OBSERVE → EVALUATE → CONTINUE/RETRY/ESCALATE → COMPLETE) and store only safe execution summaries — never hidden chain-of-thought.
- FR-8 Agents may delegate subtasks to other agents, with loop prevention (depth, fan-out and cycle limits).

### 4.3 Tools
- FR-9 Agents act on the world only through a Tool Registry. Each tool declares input/output JSON schema, permission level, timeout, retry policy, and is logged on every call.
- FR-10 Tool access is enforced per agent (allow/deny lists) and by the policy engine — not by prompt text.

### 4.4 Memory and knowledge
- FR-11 Memory layers: working, session, long-term, semantic, structured (entities/relations). All memory items carry provenance and a trust level.
- FR-12 AI-generated content enters memory as `unverified`; promotion to trusted organizational knowledge requires QA verification and/or human approval.
- FR-13 Organizations upload documents (PDF, DOCX, TXT, MD, HTML, CSV). Pipeline: parse → chunk → embed → index → hybrid retrieval (semantic + keyword + metadata filter) → cited answers.

### 4.5 Governance
- FR-14 Configurable policy rules (deny / require approval / require QA) evaluated on every tool call.
- FR-15 Human approval inbox with proposed payload and APPROVE / REJECT / EDIT / REQUEST CHANGES. Execution resumes automatically after a decision.
- FR-16 Evaluator agents score important outputs (completeness, evidence, factual support, formatting, compliance, instruction adherence). Below-threshold outputs are returned for revision with feedback; revision history is retained.
- FR-17 Budgets at organization, project, agent and task level. Near the limit, the system downgrades models and reduces iterations; at the limit, it asks a human whether to continue.

### 4.6 Model gateway
- FR-18 Multiple providers behind one gateway. Routing by purpose (extraction, planning, long context, vision), circuit breaking and fallback to secondary providers.
- FR-19 Every model call records model, latency, tokens, estimated cost and errors.

### 4.7 Observability and audit
- FR-20 Live execution monitor with the delegation graph, current action, tool calls, failures, retries, cost, tokens and duration.
- FR-21 Execution replay: a full timeline of every run.
- FR-22 Tamper-evident (hash-chained) audit log of every security-relevant action.

### 4.8 Reliability
- FR-23 Recovery from provider outage, tool timeouts, malformed model output, invalid API responses, agent loops, permission denial, budget exhaustion and worker/service restarts via retry, backoff, checkpoints, alternate model, alternate agent and human escalation.

### 4.9 Multi-tenancy and security
- FR-24 Strict tenant isolation across all entities; RBAC; encrypted secrets; rate limits; input sanitisation; webhook signature verification; prompt-injection defences treating retrieved content as untrusted data.

## 5. Flagship scenarios

1. **Autonomous B2B Sales Intelligence Team** — find 100 qualified UK companies (200–5,000 employees) for an Enterprise Device Control Platform, enrich, score with configurable criteria, identify decision-maker roles, write personalised outreach, QA + compliance check, rank, human-approve the top 20, then create CRM opportunities.
2. **Autonomous Customer Support Team** — signed webhook ticket → triage → account lookup → knowledge retrieval with citations → technical solution → QA → risk evaluation → auto-reply (low risk) or human approval (high risk) → ticket update, summary, knowledge insight, issue classification, analytics event.

## 6. Non-functional requirements

| Area | Requirement |
|---|---|
| Scalability | Stateless API and worker tiers, horizontally scalable; queue-based dispatch; DB-leased tasks |
| Availability | Workers can die at any point; leased tasks are recovered from checkpoints |
| Latency | Control-plane API p95 < 200 ms (excluding LLM work) |
| Security | OWASP ASVS L2 controls for the API; least privilege for agents |
| Auditability | 100 % of tool calls, model calls, approvals and admin changes recorded |
| Portability | Runs fully offline in dev (local providers) and against real providers in prod |

## 7. Out of scope (v1)

- Visual workflow builder for arbitrary DAG authoring (plans come from the Supervisor/templates).
- Fine-tuning pipelines.
- Billing/invoicing.

## 8. Success metrics

- Objective completion rate without human rework.
- % of sensitive actions intercepted by policy before execution (target 100 %).
- Cost per completed objective vs budget.
- Mean time to recover from injected faults.
