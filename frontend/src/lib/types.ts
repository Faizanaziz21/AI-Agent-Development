export type Json = Record<string, unknown>;

export interface Base {
  id: string;
  org_id: string;
  created_at: string;
}

export interface User extends Base {
  email: string;
  name: string;
  role: string;
  title: string;
  is_active: boolean;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  monthly_budget_usd: number;
  settings: { quality_threshold?: number; default_project_budget_usd?: number } & Json;
  created_at: string;
}

export interface OrgDetail extends Organization {
  workspaces: { id: string; name: string; description: string }[];
  project_counts: Record<string, number>;
  user_count: number;
  webhook_url: string;
}

export interface Me {
  user: User;
  organization: Organization;
  permissions: string[];
}

export interface Project extends Base {
  workspace_id: string;
  name: string;
  description: string;
  template: string;
  status: string;
  budget_usd: number;
  spent_usd: number;
  tokens_used: number;
  created_by: string | null;
  deliverable: Json | null;
  chaos: Record<string, number>;
  started_at: string | null;
  completed_at: string | null;
  task_counts?: Record<string, number>;
  tasks_total?: number;
  progress?: number;
  pending_approvals?: number;
}

export interface ProjectDetail extends Project {
  objective?: { text: string; parameters: Json } | null;
  plan?: { summary: string; version: number; strategy: Json; graph: Json } | null;
  error?: string | null;
}

export interface Task extends Base {
  project_id: string;
  plan_id: string;
  parent_task_id: string | null;
  key: string;
  title: string;
  description: string;
  capability: string;
  agent_key: string;
  agent_instance: string;
  status: string;
  depends_on: string[];
  input?: Json;
  output?: Json | null;
  output_summary: string;
  current_action: string;
  attempt: number;
  max_attempts: number;
  revision: number;
  delegation_depth: number;
  priority: number;
  budget_usd: number;
  spent_usd: number;
  tokens_used: number;
  tool_calls: number;
  failures: number;
  retries: number;
  requires_review: boolean;
  quality_score: number | null;
  feedback: string[];
  error: string | null;
  checkpoint?: Json | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface GraphNode {
  id: string;
  key: string;
  title: string;
  status: string;
  agent_key: string;
  agent_name: string;
  agent_instance: string;
  color: string;
  kind: string;
  capability: string;
  quality_score: number | null;
  revision: number;
  attempt: number;
  spent_usd: number;
  current_action: string;
  output_summary: string;
  depth: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  count?: number;
}

export interface AgentGraphNode {
  id: string;
  name: string;
  role: string;
  color: string;
  tasks: number;
  active: number;
  completed: number;
  spent_usd: number;
}

export interface ProjectGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  agents: { nodes: AgentGraphNode[]; edges: GraphEdge[] };
}

export interface EventItem {
  id: string;
  type: string;
  project_id: string | null;
  task_id: string | null;
  agent_key: string | null;
  message: string;
  payload: Json;
  created_at: string;
}

export interface ExecutionStep extends Base {
  execution_id: string;
  task_id: string;
  agent_key: string;
  seq: number;
  phase: string;
  action: string;
  tool_name: string | null;
  reason_summary: string;
  output_summary: string;
  confidence: number | null;
  next_action: string;
  flags: string[];
  duration_ms: number;
}

export interface Execution extends Base {
  task_id: string;
  agent_key: string;
  attempt: number;
  status: string;
  worker_id: string;
  iterations: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  models_used: string[];
  error: string | null;
  ended_at: string | null;
  steps: ExecutionStep[];
}

export interface ToolCall extends Base {
  project_id: string | null;
  task_id: string | null;
  agent_key: string;
  tool_name: string;
  arguments: Json;
  result: Json | null;
  status: string;
  error: string | null;
  attempts: number;
  duration_ms: number;
  policy_decision: { effect?: string; required_role?: string; matched?: { name: string; effect: string }[]; flags?: string[] } & Json;
}

export interface Evaluation extends Base {
  task_id: string;
  evaluator_agent: string;
  revision: number;
  scores: Record<string, number>;
  overall: number;
  threshold: number;
  passed: boolean;
  feedback: string;
  issues: string[];
}

export interface ModelUsage extends Base {
  project_id: string | null;
  task_id: string | null;
  agent_key: string;
  provider: string;
  model: string;
  purpose: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  success: boolean;
  error: string | null;
  fallback_from: string | null;
  downgraded: boolean;
}

export interface Approval extends Base {
  project_id: string;
  task_id: string | null;
  tool_call_id: string | null;
  agent_key: string;
  action_type: string;
  tool_name: string;
  title: string;
  reason: string;
  payload: Json;
  edited_payload: Json | null;
  risk_level: string;
  required_role: string;
  policy_refs: { id?: string; name: string; effect?: string; description?: string }[];
  status: string;
  decided_by: string | null;
  decided_at: string | null;
  comment: string;
  project_name?: string | null;
  can_decide?: boolean;
}

export interface TaskDetail {
  task: Task;
  agent: AgentDefinition | null;
  executions: Execution[];
  tool_calls: ToolCall[];
  evaluations: Evaluation[];
  revisions: { revision: number; feedback: string; output_summary: string; created_at: string }[];
  model_usage: ModelUsage[];
  approvals: Approval[];
  children: Task[];
  dependencies: Task[];
  reviews: Task[];
}

export interface AgentDefinition extends Base {
  key: string;
  name: string;
  description: string;
  role: string;
  system_instructions: string;
  allowed_tools: string[];
  prohibited_tools: string[];
  model_policy: { preferred_tier?: string; preferred_provider?: string } & Json;
  max_iterations: number;
  token_budget: number;
  cost_budget_usd: number;
  memory_config: { session?: boolean; long_term?: boolean; semantic_top_k?: number };
  escalation_rules: { on_failure?: string; alternate_agent?: string } & Json;
  approval_rules: Json[];
  can_delegate_to: string[];
  capabilities: string[];
  color: string;
  is_builtin: boolean;
  enabled: boolean;
  version: number;
  stats?: { tasks: number; cost_usd: number; avg_quality: number | null };
}

export interface ToolDefinition extends Base {
  name: string;
  description: string;
  category: string;
  input_schema: { properties?: Record<string, { type?: string; description?: string } & Json>; required?: string[] } & Json;
  output_schema: Json;
  permission_level: string;
  timeout_seconds: number;
  retry_policy: { max_attempts?: number; backoff_seconds?: number; backoff_multiplier?: number };
  requires_approval: boolean;
  implementation: string;
  config: Json;
  enabled: boolean;
  untrusted_output: boolean;
  sensitive_args: string[];
  agents: string[];
  stats: { calls: number; by_status: Record<string, number>; avg_ms: number | null };
}

export interface Policy extends Base {
  name: string;
  description: string;
  scope: "tool_call" | "knowledge_access" | "delegation";
  condition: Json;
  effect: "deny" | "require_approval" | "require_qa" | "allow";
  required_role: string;
  priority: number;
  enabled: boolean;
}

export interface KnowledgeSource extends Base {
  name: string;
  source_type: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  classification: string;
  tags: string[];
  status: string;
  chunk_count: number;
  injection_flags: number;
  error: string | null;
  restricted?: boolean;
}

export interface Passage {
  id: string;
  source: string;
  section?: string;
  chunk_id: string;
  source_id?: string;
  text: string;
  classification: string;
  source_type: string;
  semantic_rank: number | null;
  keyword_rank: number | null;
  injection_risk: number;
  page?: number | null;
  score?: number;
}

export interface MemoryItem extends Base {
  project_id: string | null;
  task_id: string | null;
  layer: string;
  kind: string;
  key: string;
  content: string;
  data: Json;
  trust_level: string;
  source_type: string;
  source_ref: string;
  created_by_agent: string | null;
  confidence: number;
  verified_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  score?: number;
}

export interface Entity extends Base {
  project_id: string | null;
  type: string;
  name: string;
  attributes: Json;
  provenance: Record<string, ({ source?: string; confidence?: number; agent?: string; at?: string } & Json) | unknown>;
  trust_level: string;
}

export interface Relationship extends Base {
  source_entity_id: string;
  target_entity_id: string;
  type: string;
  attributes: Json;
  provenance: Json;
}

export interface AuditRecord extends Base {
  seq: number;
  actor_type: string;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  project_id: string | null;
  details: Json;
  ip: string | null;
  prev_hash: string;
  hash: string;
}

export interface Bucket {
  key: string;
  calls: number;
  cost_usd: number;
  tokens: number;
  avg_latency_ms: number;
}

export interface CostSummary {
  period_days: number;
  totals: {
    calls: number;
    cost_usd: number;
    input_tokens: number;
    output_tokens: number;
    avg_latency_ms: number;
    failures: number;
    fallbacks: number;
    downgrades: number;
  };
  budget: { monthly_budget_usd: number; month_spent_usd: number; ratio: number };
  by_provider: Bucket[];
  by_model: Bucket[];
  by_agent: Bucket[];
  by_purpose: Bucket[];
  daily: { day: string; cost_usd: number; calls: number }[];
  by_project: { id: string; name: string; status: string; budget_usd: number; spent_usd: number; tokens: number; ratio: number | null }[];
}

export interface SystemStatus {
  environment: string;
  database: string;
  queue: { backend: string; depth: number };
  workers: { embedded: boolean; concurrency: number; busy: number; processed: number; name: string };
  providers: { provider: string; available: boolean; circuit: string }[];
  provider_order: string[];
  models: {
    name: string;
    provider: string;
    tier: string;
    context_window: number;
    input_cost_per_mtok: number;
    output_cost_per_mtok: number;
  }[];
  purpose_tiers: Record<string, string>;
  limits: Record<string, number>;
  embedding_provider: string;
  vector_backend: string;
  tasks: Record<string, number>;
  active_tasks: number;
}

export interface Notification extends Base {
  kind: string;
  title: string;
  body: string;
  link: string;
  read: boolean;
}

export interface Template {
  key: string;
  name: string;
  objective: string;
  parameters: Json;
  budget_usd: number;
}

export interface Templates {
  templates: Template[];
  demo_tickets: { customer_email: string; subject: string; body: string }[];
  chaos_keys: string[];
}

export interface OutboundMessage extends Base {
  project_id: string | null;
  channel: string;
  status: string;
  recipient: string;
  subject: string;
  body: string;
  meta: Json;
}

export interface SupportTicket extends Base {
  project_id: string | null;
  external_id: string;
  customer_email: string;
  subject: string;
  body: string;
  status: string;
  priority: string;
  category: string;
  product_area: string;
  risk_level: string;
  resolution: string;
  summary: string;
  attributes: Json;
}

export interface Secret extends Base {
  name: string;
  description: string;
  preview: string;
  updated_at?: string;
}
