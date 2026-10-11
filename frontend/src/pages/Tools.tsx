import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Lock, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import {
  Badge, Button, Card, Drawer, Empty, ErrorBox, Field, Input, JsonView, KV, Modal, PageHeader, Select, Spinner, Table, Tabs, Textarea, Toggle,
} from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { compact, titleCase } from "../lib/format";
import type { Policy, ToolDefinition } from "../lib/types";

const ROLES = ["approver", "finance_manager", "compliance_officer", "operator", "admin", "owner"];
const EFFECT_STYLE: Record<string, string> = {
  deny: "bg-rose-50 text-rose-700 ring-rose-200",
  require_qa: "bg-violet-50 text-violet-700 ring-violet-200",
  require_approval: "bg-amber-50 text-amber-700 ring-amber-200",
  allow: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};
const PERMISSION_STYLE: Record<string, string> = {
  read: "bg-slate-100 text-slate-600 ring-slate-200",
  write: "bg-sky-50 text-sky-700 ring-sky-200",
  external: "bg-amber-50 text-amber-700 ring-amber-200",
  privileged: "bg-rose-50 text-rose-700 ring-rose-200",
};

export default function Tools() {
  const [tab, setTab] = useState<"tools" | "policies">("tools");
  return (
    <div>
      <PageHeader
        title="Tools & guardrails"
        description="Every tool call passes schema validation, agent permissions, policy evaluation, loop detection and audit logging before it runs. Policies are data and take effect immediately."
      />
      <Tabs
        className="mb-5"
        value={tab}
        onChange={setTab}
        tabs={[{ key: "tools", label: "Tool registry" }, { key: "policies", label: "Policies" }]}
      />
      {tab === "tools" ? <ToolRegistry /> : <Policies />}
    </div>
  );
}

function ToolRegistry() {
  const tools = useQuery({ queryKey: ["tools"], queryFn: () => api.get<ToolDefinition[]>("/tools") });
  const [selected, setSelected] = useState<ToolDefinition | null>(null);
  const byCategory = useMemo(() => {
    const m = new Map<string, ToolDefinition[]>();
    for (const t of tools.data ?? []) m.set(t.category, [...(m.get(t.category) ?? []), t]);
    return [...m.entries()];
  }, [tools.data]);

  if (tools.isLoading) return <Spinner />;
  if (tools.error) return <ErrorBox error={tools.error} />;
  return (
    <div className="space-y-6">
      {byCategory.map(([cat, list]) => (
        <Card key={cat} title={titleCase(cat)} subtitle={`${list.length} tools`} bodyClass="">
          <Table head={["Tool", "Permission", "Safeguards", "Agents", "Calls", "Avg latency", "Enabled"]}>
            {list.map((t) => {
              const failed = (t.stats.by_status.FAILED ?? 0) + (t.stats.by_status.DENIED ?? 0);
              return (
                <tr key={t.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelected(t)}>
                  <td className="max-w-sm px-3 py-2.5">
                    <div className="font-mono text-[13px] font-medium text-slate-900">{t.name}</div>
                    <div className="truncate text-xs text-slate-500">{t.description}</div>
                  </td>
                  <td className="px-3 py-2.5"><Badge className={PERMISSION_STYLE[t.permission_level]}>{t.permission_level}</Badge></td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {t.requires_approval && <Badge className="bg-amber-50 text-amber-700 ring-amber-200"><Lock className="h-3 w-3" /> approval</Badge>}
                      {t.untrusted_output && <Badge className="bg-rose-50 text-rose-700 ring-rose-200" title="Output is treated as untrusted data and scanned for prompt injection"><ShieldAlert className="h-3 w-3" /> untrusted output</Badge>}
                      {t.sensitive_args.length > 0 && <Badge title={`Taint-checked arguments: ${t.sensitive_args.join(", ")}`}>taint-checked</Badge>}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-600">{t.agents.length || <span className="text-slate-400">none</span>}</td>
                  <td className="px-3 py-2.5 text-xs tabular-nums">
                    {compact(t.stats.calls)}
                    {failed > 0 && <span className="ml-1 text-rose-600">({failed} failed/denied)</span>}
                  </td>
                  <td className="px-3 py-2.5 text-xs tabular-nums text-slate-600">{t.stats.avg_ms != null ? `${t.stats.avg_ms} ms` : "—"}</td>
                  <td className="px-3 py-2.5">
                    <Badge className={t.enabled ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : ""}>{t.enabled ? "on" : "off"}</Badge>
                  </td>
                </tr>
              );
            })}
          </Table>
        </Card>
      ))}
      <ToolDrawer tool={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function ToolDrawer({ tool, onClose }: { tool: ToolDefinition | null; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<{ enabled: boolean; requires_approval: boolean; timeout_seconds: number; max_attempts: number } | null>(null);
  const [lastId, setLastId] = useState<string | null>(null);
  if (tool && tool.id !== lastId) {
    setLastId(tool.id);
    setDraft({ enabled: tool.enabled, requires_approval: tool.requires_approval, timeout_seconds: tool.timeout_seconds, max_attempts: tool.retry_policy.max_attempts ?? 1 });
  }
  const save = useMutation({
    mutationFn: () =>
      api.patch(`/tools/${tool!.id}`, {
        enabled: draft!.enabled,
        requires_approval: draft!.requires_approval,
        timeout_seconds: draft!.timeout_seconds,
        retry_policy: { ...tool!.retry_policy, max_attempts: draft!.max_attempts },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tools"] });
      onClose();
    },
  });
  const editable = can("tool.write");
  const props = Object.entries(tool?.input_schema.properties ?? {});
  return (
    <Drawer
      open={!!tool}
      onClose={onClose}
      title={tool?.name ?? ""}
      subtitle={tool?.description}
      actions={editable && <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>}
    >
      {tool && draft && (
        <div className="space-y-5 p-5">
          <KV items={[
            ["Category", titleCase(tool.category)],
            ["Permission level", tool.permission_level],
            ["Implementation", <code key="i" className="text-xs">{tool.implementation}</code>],
            ["Agents allowed", tool.agents.length ? tool.agents.map(titleCase).join(", ") : "none"],
          ]} />
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex items-center gap-2 text-sm">
              <Toggle checked={draft.enabled} disabled={!editable} onChange={(v) => setDraft({ ...draft, enabled: v })} label="enabled" /> Enabled
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Toggle checked={draft.requires_approval} disabled={!editable} onChange={(v) => setDraft({ ...draft, requires_approval: v })} label="requires approval" /> Always require approval
            </label>
            <Field label="Timeout (seconds)">
              <Input type="number" min={1} max={600} disabled={!editable} value={draft.timeout_seconds} onChange={(e) => setDraft({ ...draft, timeout_seconds: Number(e.target.value) })} />
            </Field>
            <Field label="Max attempts" hint="Transient failures retry with exponential backoff.">
              <Input type="number" min={1} max={10} disabled={!editable} value={draft.max_attempts} onChange={(e) => setDraft({ ...draft, max_attempts: Number(e.target.value) })} />
            </Field>
          </div>
          <ErrorBox error={save.error} />
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Input schema</h4>
            {props.length === 0 ? <p className="text-sm text-slate-500">No arguments.</p> : (
              <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                {props.map(([k, v]) => (
                  <li key={k} className="px-3 py-2 text-sm">
                    <span className="font-mono text-[13px] text-slate-900">{k}</span>
                    <span className="ml-2 text-xs text-slate-500">{String(v.type ?? "any")}</span>
                    {tool.input_schema.required?.includes(k) && <span className="ml-2 text-[11px] text-rose-600">required</span>}
                    {tool.sensitive_args.includes(k) && <span className="ml-2 text-[11px] text-amber-600">taint-checked</span>}
                    {v.description && <div className="text-xs text-slate-500">{v.description}</div>}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Calls by outcome</h4>
            {Object.keys(tool.stats.by_status).length === 0 ? <p className="text-sm text-slate-500">Not called yet.</p> : (
              <div className="flex flex-wrap gap-2">
                {Object.entries(tool.stats.by_status).map(([s, n]) => <Badge key={s}>{titleCase(s)}: {n}</Badge>)}
              </div>
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}

const EMPTY_POLICY: Omit<Policy, "id" | "org_id" | "created_at"> = {
  name: "",
  description: "",
  scope: "tool_call",
  condition: { all: [{ field: "tool", op: "eq", value: "email_send" }, { field: "facts.recipient_count", op: "gt", value: 50 }] },
  effect: "require_approval",
  required_role: "approver",
  priority: 100,
  enabled: true,
};

function Policies() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const policies = useQuery({ queryKey: ["policies"], queryFn: () => api.get<Policy[]>("/policies") });
  const [editing, setEditing] = useState<Policy | null | "new">(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/policies/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["policies"] }),
  });
  const toggle = useMutation({
    mutationFn: (p: Policy) => api.put(`/policies/${p.id}`, { ...p, enabled: !p.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["policies"] }),
  });
  const writable = can("policy.write");

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_420px]">
      <Card
        title="Policies"
        subtitle="Precedence: deny › require QA › require approval › allow. Lower priority numbers are evaluated first."
        actions={writable && <Button size="sm" variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setEditing("new")}>New policy</Button>}
        bodyClass=""
      >
        {policies.isLoading ? <Spinner /> : (policies.data ?? []).length === 0 ? <Empty title="No policies" /> : (
          <Table head={["Policy", "Scope", "Effect", "Approver", "Priority", "Enabled", ""]}>
            {policies.data!.map((p) => (
              <tr key={p.id} className="align-top">
                <td className="max-w-md px-3 py-2.5">
                  <button className="text-left font-medium text-slate-900 hover:text-brand-700" onClick={() => setEditing(p)}>{p.name}</button>
                  <div className="text-xs text-slate-500">{p.description}</div>
                </td>
                <td className="px-3 py-2.5 text-xs">{titleCase(p.scope)}</td>
                <td className="px-3 py-2.5"><Badge className={EFFECT_STYLE[p.effect]}>{titleCase(p.effect)}</Badge></td>
                <td className="px-3 py-2.5 text-xs">{p.effect === "require_approval" ? titleCase(p.required_role) : "—"}</td>
                <td className="px-3 py-2.5 text-xs tabular-nums">{p.priority}</td>
                <td className="px-3 py-2.5"><Toggle checked={p.enabled} disabled={!writable || toggle.isPending} onChange={() => toggle.mutate(p)} label={`toggle ${p.name}`} /></td>
                <td className="px-3 py-2.5 text-right">
                  {writable && (
                    <Button size="sm" variant="ghost" aria-label={`Delete ${p.name}`} icon={<Trash2 className="h-3.5 w-3.5" />}
                      onClick={() => confirm(`Delete policy "${p.name}"?`) && remove.mutate(p.id)} />
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
        <div className="px-4 pb-3"><ErrorBox error={remove.error ?? toggle.error} /></div>
      </Card>
      <PolicySimulator />
      {editing && <PolicyEditor policy={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function PolicyEditor({ policy, onClose }: { policy: Policy | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState(() => (policy ? { ...policy } : { ...EMPTY_POLICY }));
  const [cond, setCond] = useState(() => JSON.stringify(d.condition, null, 2));
  const [condError, setCondError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body: typeof d) => (policy ? api.put(`/policies/${policy.id}`, body) : api.post("/policies", body)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["policies"] });
      onClose();
    },
  });
  const submit = () => {
    try {
      const condition = JSON.parse(cond);
      setCondError(null);
      save.mutate({ ...d, condition });
    } catch (e) {
      setCondError(`Condition is not valid JSON: ${(e as Error).message}`);
    }
  };
  return (
    <Modal
      open
      wide
      onClose={onClose}
      title={policy ? `Edit ${policy.name}` : "New policy"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={submit}>Save policy</Button></>}
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><Input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></Field>
          <Field label="Scope">
            <Select value={d.scope} onChange={(e) => setD({ ...d, scope: e.target.value as Policy["scope"] })}>
              <option value="tool_call">Tool call</option>
              <option value="knowledge_access">Knowledge access</option>
              <option value="delegation">Delegation</option>
            </Select>
          </Field>
        </div>
        <Field label="Description"><Input value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Effect">
            <Select value={d.effect} onChange={(e) => setD({ ...d, effect: e.target.value as Policy["effect"] })}>
              {Object.keys(EFFECT_STYLE).map((e) => <option key={e} value={e}>{titleCase(e)}</option>)}
            </Select>
          </Field>
          <Field label="Required approver role">
            <Select value={d.required_role} onChange={(e) => setD({ ...d, required_role: e.target.value })} disabled={d.effect !== "require_approval"}>
              {ROLES.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
            </Select>
          </Field>
          <Field label="Priority"><Input type="number" min={0} max={10000} value={d.priority} onChange={(e) => setD({ ...d, priority: Number(e.target.value) })} /></Field>
        </div>
        <Field
          label="Condition"
          error={condError}
          hint={<>Combine with <code>all</code> / <code>any</code> / <code>not</code>. Leaves: <code>{"{field, op, value}"}</code>, ops eq ne gt gte lt lte in not_in contains exists matches. Tool-call fields include <code>tool</code>, <code>agent_key</code>, <code>args.*</code>, <code>facts.*</code>, <code>tainted</code>, <code>project_template</code>.</>}
        >
          <Textarea rows={10} className="font-mono text-xs" value={cond} onChange={(e) => setCond(e.target.value)} />
        </Field>
        <ErrorBox error={save.error} />
      </div>
    </Modal>
  );
}

const SIM_EXAMPLE = {
  tool: "email_send",
  agent_key: "outreach",
  args: { to: ["cfo@acme.example"], subject: "Intro" },
  facts: { recipient_count: 120, external: true },
  tainted: false,
};

function PolicySimulator() {
  const [scope, setScope] = useState<Policy["scope"]>("tool_call");
  const [ctx, setCtx] = useState(JSON.stringify(SIM_EXAMPLE, null, 2));
  const [err, setErr] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (context: unknown) => api.post<{ effect: string; required_role: string; matched: { name: string; effect: string; description: string }[] }>("/policies/simulate", { scope, context }),
  });
  return (
    <Card title="Policy simulator" subtitle="Dry-run a context against the live policy set" actions={<FlaskConical className="h-4 w-4 text-slate-400" />}>
      <div className="space-y-3">
        <Select value={scope} onChange={(e) => setScope(e.target.value as Policy["scope"])}>
          <option value="tool_call">Tool call</option>
          <option value="knowledge_access">Knowledge access</option>
          <option value="delegation">Delegation</option>
        </Select>
        <Textarea rows={12} className="font-mono text-xs" value={ctx} onChange={(e) => setCtx(e.target.value)} />
        {err && <p className="text-xs text-rose-600">{err}</p>}
        <Button
          variant="primary"
          className="w-full"
          loading={run.isPending}
          onClick={() => {
            try {
              setErr(null);
              run.mutate(JSON.parse(ctx));
            } catch (e) {
              setErr((e as Error).message);
            }
          }}
        >
          Evaluate
        </Button>
        <ErrorBox error={run.error} />
        {run.data && (
          <div className="space-y-2 rounded-lg border border-slate-200 p-3">
            <div className="flex items-center gap-2 text-sm">
              Decision <Badge className={EFFECT_STYLE[run.data.effect]}>{titleCase(run.data.effect)}</Badge>
              {run.data.effect === "require_approval" && <span className="text-xs text-slate-500">by {titleCase(run.data.required_role)}</span>}
            </div>
            {run.data.matched.length === 0 ? <p className="text-xs text-slate-500">No policy matched.</p> : (
              <ul className="space-y-1 text-xs">
                {run.data.matched.map((m, i) => (
                  <li key={i}><Badge className={EFFECT_STYLE[m.effect]}>{m.effect}</Badge> <span className="font-medium">{m.name}</span> <span className="text-slate-500">{m.description}</span></li>
                ))}
              </ul>
            )}
            <JsonView value={run.data} collapsed />
          </div>
        )}
      </div>
    </Card>
  );
}
