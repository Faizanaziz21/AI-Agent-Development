import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge, Button, Card, ErrorBox, Field, Input, Modal, PageHeader, Select, Spinner, TagInput, Textarea, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { pct, usd } from "../lib/format";
import type { AgentDefinition, SystemStatus, ToolDefinition } from "../lib/types";

type Draft = Omit<AgentDefinition, "id" | "org_id" | "created_at" | "is_builtin" | "version" | "stats">;

const BLANK: Draft = {
  key: "",
  name: "",
  role: "",
  description: "",
  system_instructions: "",
  allowed_tools: [],
  prohibited_tools: [],
  model_policy: { preferred_tier: "standard" },
  max_iterations: 8,
  token_budget: 60000,
  cost_budget_usd: 2,
  memory_config: { session: true, long_term: true, semantic_top_k: 3 },
  escalation_rules: { on_failure: "human" },
  approval_rules: [],
  can_delegate_to: [],
  capabilities: [],
  color: "#6366f1",
  enabled: true,
};

function AgentEditor({ agent, open, onClose, agents, tools }: {
  agent: AgentDefinition | null;
  open: boolean;
  onClose: () => void;
  agents: AgentDefinition[];
  tools: ToolDefinition[];
}) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const status = useQuery({ queryKey: ["system", "status"], queryFn: () => api.get<SystemStatus>("/system/status"), enabled: open });
  const [d, setD] = useState<Draft>(BLANK);
  const [caps, setCaps] = useState("");
  useEffect(() => {
    if (!open) return;
    const src = agent ? { ...BLANK, ...agent } : BLANK;
    setD(src);
    setCaps(src.capabilities.join(", "));
  }, [agent, open]);

  const editable = can("agent.write");
  const save = useMutation({
    mutationFn: () => {
      const body = { ...d, capabilities: caps.split(",").map((c) => c.trim()).filter(Boolean) };
      if (agent) {
        const { key: _key, ...rest } = body;
        return api.patch<AgentDefinition>(`/agents/${agent.id}`, rest);
      }
      return api.post<AgentDefinition>("/agents", body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      onClose();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/agents/${agent!.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      onClose();
    },
  });
  const tiers = [...new Set(status.data?.models.map((m) => m.tier) ?? ["economy", "standard", "premium"])];
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));

  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title={agent ? `${agent.name}${agent.is_builtin ? " (built-in)" : ""} · v${agent.version}` : "New agent"}
      footer={
        editable && (
          <>
            {agent && !agent.is_builtin && (
              <Button variant="ghost" className="mr-auto text-rose-600" icon={<Trash2 className="h-4 w-4" />} loading={remove.isPending} onClick={() => confirm(`Delete ${agent.name}?`) && remove.mutate()}>
                Delete
              </Button>
            )}
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
              {agent ? "Save new version" : "Create agent"}
            </Button>
          </>
        )
      }
    >
      <fieldset disabled={!editable} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Key" hint="Lowercase identifier used in plans">
            <Input value={d.key} disabled={!!agent} onChange={(e) => set("key", e.target.value)} placeholder="pricing_analyst" />
          </Field>
          <Field label="Name">
            <Input value={d.name} onChange={(e) => set("name", e.target.value)} />
          </Field>
          <Field label="Role">
            <Input value={d.role} onChange={(e) => set("role", e.target.value)} />
          </Field>
        </div>
        <Field label="Description">
          <Input value={d.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
        <Field label="System instructions" hint="Combined with the platform's non-overridable safety preamble at run time.">
          <Textarea rows={6} value={d.system_instructions} onChange={(e) => set("system_instructions", e.target.value)} className="font-mono text-xs" />
        </Field>
        <Field label="Allowed tools" hint="Tools outside this list are rejected by the executor before any policy check.">
          <TagInput value={d.allowed_tools} onChange={(v) => set("allowed_tools", v)} options={tools.map((t) => t.name)} placeholder="Add tool…" />
        </Field>
        <Field label="Prohibited tools">
          <TagInput value={d.prohibited_tools} onChange={(v) => set("prohibited_tools", v)} options={tools.map((t) => t.name)} placeholder="Add tool…" />
        </Field>
        <Field label="May delegate to" hint="Delegation is also bounded by max depth 3 and fan-out 8, with cycle detection.">
          <TagInput value={d.can_delegate_to} onChange={(v) => set("can_delegate_to", v)} options={agents.map((a) => a.key).filter((k) => k !== d.key)} placeholder="Add agent…" />
        </Field>
        <Field label="Capabilities" hint="Comma-separated task capabilities this agent can be planned for.">
          <Input value={caps} onChange={(e) => setCaps(e.target.value)} className="font-mono text-xs" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Model tier">
            <Select value={String(d.model_policy.preferred_tier ?? "standard")} onChange={(e) => set("model_policy", { ...d.model_policy, preferred_tier: e.target.value })}>
              {tiers.map((t) => <option key={t} value={t}>{t}</option>)}
            </Select>
          </Field>
          <Field label="Max iterations">
            <Input type="number" min={1} max={50} value={d.max_iterations} onChange={(e) => set("max_iterations", Number(e.target.value))} />
          </Field>
          <Field label="Token budget">
            <Input type="number" min={1000} step={1000} value={d.token_budget} onChange={(e) => set("token_budget", Number(e.target.value))} />
          </Field>
          <Field label="Cost budget (USD)">
            <Input type="number" min={0} step={0.5} value={d.cost_budget_usd} onChange={(e) => set("cost_budget_usd", Number(e.target.value))} />
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="On failure, reassign to" hint="Tried once before escalating to a human approver.">
            <Select value={String(d.escalation_rules.alternate_agent ?? "")} onChange={(e) => set("escalation_rules", { ...d.escalation_rules, alternate_agent: e.target.value || undefined })}>
              <option value="">Nobody (escalate to human)</option>
              {agents.filter((a) => a.key !== d.key).map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
            </Select>
          </Field>
          <Field label="Semantic memory top-k">
            <Input type="number" min={0} max={20} value={d.memory_config.semantic_top_k ?? 3} onChange={(e) => set("memory_config", { ...d.memory_config, semantic_top_k: Number(e.target.value) })} />
          </Field>
          <Field label="Colour">
            <Input type="color" value={d.color} onChange={(e) => set("color", e.target.value)} className="h-9 p-1" />
          </Field>
        </div>
        <div className="flex flex-wrap gap-6 text-sm">
          {(["session", "long_term"] as const).map((k) => (
            <label key={k} className="flex items-center gap-2">
              <Toggle checked={!!d.memory_config[k]} onChange={(v) => set("memory_config", { ...d.memory_config, [k]: v })} label={k} />
              {k === "session" ? "Session memory" : "Long-term memory"}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <Toggle checked={d.enabled} onChange={(v) => set("enabled", v)} label="enabled" />
            Enabled
          </label>
        </div>
        <ErrorBox error={save.error ?? remove.error} />
      </fieldset>
    </Modal>
  );
}

export default function Agents() {
  const { can } = useAuth();
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api.get<AgentDefinition[]>("/agents") });
  const tools = useQuery({ queryKey: ["tools"], queryFn: () => api.get<ToolDefinition[]>("/tools") });
  const [editing, setEditing] = useState<AgentDefinition | null>(null);
  const [open, setOpen] = useState(false);
  const list = agents.data ?? [];

  return (
    <div>
      <PageHeader
        title="Agent library"
        description="Agents are data, not code: instructions, tool permissions, model policy, budgets, memory and escalation rules are versioned per tenant."
        actions={can("agent.write") && <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => { setEditing(null); setOpen(true); }}>New agent</Button>}
      />
      {agents.isLoading ? (
        <Spinner />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {list.map((a) => (
            <button key={a.id} onClick={() => { setEditing(a); setOpen(true); }} className={clsx("text-left transition hover:-translate-y-0.5", !a.enabled && "opacity-60")}>
              <Card className="h-full hover:shadow-md" bodyClass="p-4">
                <div className="flex items-start gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-white" style={{ background: a.color }}>
                    <Bot className="h-5 w-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-semibold text-slate-900">{a.name}</span>
                      {!a.enabled && <Badge>disabled</Badge>}
                      {!a.is_builtin && <Badge className="bg-brand-50 text-brand-700 ring-brand-200">custom</Badge>}
                    </div>
                    <div className="text-xs text-slate-500">{a.role} · v{a.version} · {String(a.model_policy.preferred_tier ?? "standard")} tier</div>
                  </div>
                </div>
                <p className="mt-3 line-clamp-2 min-h-10 text-sm text-slate-600">{a.description}</p>
                <div className="mt-3 flex flex-wrap gap-1">
                  {a.allowed_tools.slice(0, 6).map((t) => <Badge key={t} className="bg-slate-50 font-mono text-slate-600 ring-slate-200">{t}</Badge>)}
                  {a.allowed_tools.length > 6 && <Badge>+{a.allowed_tools.length - 6}</Badge>}
                </div>
                <div className="mt-4 grid grid-cols-3 gap-2 border-t border-slate-100 pt-3 text-center">
                  <div><div className="text-sm font-semibold">{a.stats?.tasks ?? 0}</div><div className="text-[11px] text-slate-500">tasks</div></div>
                  <div><div className="text-sm font-semibold">{usd(a.stats?.cost_usd ?? 0, 2)}</div><div className="text-[11px] text-slate-500">spend</div></div>
                  <div><div className="text-sm font-semibold">{a.stats?.avg_quality != null ? pct(a.stats.avg_quality) : "—"}</div><div className="text-[11px] text-slate-500">avg QA</div></div>
                </div>
              </Card>
            </button>
          ))}
        </div>
      )}
      <AgentEditor agent={editing} open={open} onClose={() => setOpen(false)} agents={list} tools={tools.data ?? []} />
    </div>
  );
}
