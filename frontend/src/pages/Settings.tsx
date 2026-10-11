import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, RefreshCw, RotateCw, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, Empty, ErrorBox, Field, Input, KV, Modal, PageHeader, Select, Spinner, StatusBadge, Table, Tabs, Textarea, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { compact, timeAgo, titleCase, usd } from "../lib/format";
import type { OrgDetail, Secret, SystemStatus, User } from "../lib/types";

const ROLES = ["viewer", "approver", "finance_manager", "compliance_officer", "operator", "admin", "owner"];
const ROLE_HINT: Record<string, string> = {
  viewer: "Read-only access",
  approver: "Decides general approvals",
  finance_manager: "Decides financial approvals (refunds, budgets)",
  compliance_officer: "Decides compliance approvals, edits policies, reads audit",
  operator: "Runs projects, uploads knowledge, decides general approvals",
  admin: "Configures agents, tools, policies, users and secrets",
  owner: "Everything, including granting owner",
};

const CIRCUIT_STYLE: Record<string, string> = {
  closed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  half_open: "bg-amber-50 text-amber-700 ring-amber-200",
  open: "bg-rose-50 text-rose-700 ring-rose-200",
};

type Tab = "org" | "team" | "secrets" | "system";

export default function Settings() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>("org");
  const tabs: { key: Tab; label: string }[] = [
    { key: "org", label: "Organisation" },
    { key: "team", label: "Team & roles" },
    ...(can("settings.write") ? [{ key: "secrets" as const, label: "Secrets" }] : []),
    { key: "system", label: "System" },
  ];
  return (
    <div>
      <PageHeader title="Settings" description="Organisation guardrails, team access, encrypted integration secrets and live runtime status." />
      <Tabs className="mb-5" value={tab} onChange={setTab} tabs={tabs} />
      {tab === "org" && <OrgSettings />}
      {tab === "team" && <Team />}
      {tab === "secrets" && <Secrets />}
      {tab === "system" && <System />}
    </div>
  );
}

type CommRules = {
  require_opt_out?: boolean;
  require_sender_identity?: boolean;
  prohibited_phrases?: string[];
  blocked_recipient_domains?: string[];
  max_recipients_per_batch?: number;
  max_body_chars?: number;
};

function OrgSettings() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const org = useQuery({ queryKey: ["org"], queryFn: () => api.get<OrgDetail>("/org") });
  const [draft, setDraft] = useState<{ name: string; budget: number; threshold: number; maxRev: number; projectBudget: number; rules: CommRules } | null>(null);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [ws, setWs] = useState("");
  if (org.data && !draft) {
    const st = org.data.settings;
    setDraft({
      name: org.data.name,
      budget: org.data.monthly_budget_usd,
      threshold: Number(st.quality_threshold ?? 0.75),
      maxRev: Number(st.max_revisions ?? 2),
      projectBudget: Number(st.default_project_budget_usd ?? 20),
      rules: (st.communication_rules as CommRules) ?? {},
    });
  }
  const save = useMutation({
    mutationFn: () =>
      api.patch("/org", {
        name: draft!.name,
        monthly_budget_usd: draft!.budget,
        settings: { quality_threshold: draft!.threshold, max_revisions: draft!.maxRev, default_project_budget_usd: draft!.projectBudget, communication_rules: draft!.rules },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["org"] });
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
  });
  const rotate = useMutation({
    mutationFn: () => api.post<{ webhook_secret: string }>("/org/webhook-secret/rotate"),
    onSuccess: (d) => setRevealed(d.webhook_secret),
  });
  const addWs = useMutation({
    mutationFn: () => api.post("/org/workspaces", { name: ws }),
    onSuccess: () => {
      setWs("");
      qc.invalidateQueries({ queryKey: ["org"] });
    },
  });
  const editable = can("settings.write");
  if (org.isLoading || !draft) return <Spinner />;
  if (org.error) return <ErrorBox error={org.error} />;
  const r = draft.rules;
  const setRules = (patch: Partial<CommRules>) => setDraft({ ...draft, rules: { ...r, ...patch } });

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
      <div className="space-y-6">
        <Card title="Organisation & budgets" actions={editable && <Button variant="primary" size="sm" loading={save.isPending} onClick={() => save.mutate()}>Save changes</Button>}>
          <fieldset disabled={!editable} className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Organisation name"><Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
              <Field label="Monthly model budget (USD)" hint="Hard limit across all projects; reaching it pauses work for a budget approval.">
                <Input type="number" min={0} step={10} value={draft.budget} onChange={(e) => setDraft({ ...draft, budget: Number(e.target.value) })} />
              </Field>
              <Field label="Default project budget (USD)" hint="Pre-filled for custom-objective projects; built-in templates keep budgets sized for their workload.">
                <Input type="number" min={0.01} step={1} value={draft.projectBudget} onChange={(e) => setDraft({ ...draft, projectBudget: Number(e.target.value) })} />
              </Field>
              <Field label={`Minimum QA score: ${draft.threshold.toFixed(2)}`} hint="Floor applied to every review; individual tasks may require more.">
                <input type="range" min={0} max={1} step={0.05} value={draft.threshold} onChange={(e) => setDraft({ ...draft, threshold: Number(e.target.value) })} className="w-full accent-brand-600" />
              </Field>
              <Field label="Max QA revision rounds" hint="After this many rejected revisions the task escalates to a human.">
                <Input type="number" min={0} max={10} value={draft.maxRev} onChange={(e) => setDraft({ ...draft, maxRev: Number(e.target.value) })} />
              </Field>
            </div>
          </fieldset>
          <div className="mt-3"><ErrorBox error={save.error} /></div>
          {save.isSuccess && <p className="mt-2 text-xs text-emerald-600">Saved.</p>}
        </Card>

        <Card title="Outbound communication rules" subtitle="Enforced by the compliance check tool before any email is sent">
          <fieldset disabled={!editable} className="space-y-4">
            <div className="flex flex-wrap gap-6 text-sm">
              <label className="flex items-center gap-2">
                <Toggle checked={r.require_opt_out ?? true} onChange={(v) => setRules({ require_opt_out: v })} label="require opt-out" /> Require an opt-out line
              </label>
              <label className="flex items-center gap-2">
                <Toggle checked={r.require_sender_identity ?? true} onChange={(v) => setRules({ require_sender_identity: v })} label="require sender identity" /> Require sender identity
              </label>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Max recipients per batch"><Input type="number" min={1} value={r.max_recipients_per_batch ?? 50} onChange={(e) => setRules({ max_recipients_per_batch: Number(e.target.value) })} /></Field>
              <Field label="Max body length (characters)"><Input type="number" min={100} value={r.max_body_chars ?? 2500} onChange={(e) => setRules({ max_body_chars: Number(e.target.value) })} /></Field>
            </div>
            <Field label="Prohibited phrases" hint="One per line">
              <Textarea rows={4} value={(r.prohibited_phrases ?? []).join("\n")} onChange={(e) => setRules({ prohibited_phrases: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })}
                placeholder={"guarantee\nrisk-free\nact now"} />
            </Field>
            <Field label="Blocked recipient domains" hint="One per line — e.g. personal mailboxes for B2B outreach">
              <Textarea rows={3} value={(r.blocked_recipient_domains ?? []).join("\n")} onChange={(e) => setRules({ blocked_recipient_domains: e.target.value.split("\n").map((s) => s.trim().toLowerCase()).filter(Boolean) })}
                placeholder={"gmail.com\nyahoo.com"} />
            </Field>
            <p className="text-xs text-slate-500">Empty fields fall back to the platform defaults. Save with the button above.</p>
          </fieldset>
        </Card>
      </div>

      <div className="space-y-6">
        <Card title="Overview">
          <KV items={[
            ["Plan", <Badge key="p">{org.data!.plan}</Badge>],
            ["Slug", <code key="s" className="text-xs">{org.data!.slug}</code>],
            ["Members", org.data!.user_count],
            ["Projects", Object.values(org.data!.project_counts).reduce((a, n) => a + n, 0)],
            ["Created", timeAgo(org.data!.created_at)],
          ]} />
        </Card>
        <Card title="Workspaces">
          <ul className="mb-3 space-y-1.5 text-sm">
            {org.data!.workspaces.map((w) => <li key={w.id} className="flex items-center justify-between"><span>{w.name}</span><span className="truncate text-xs text-slate-400">{w.description}</span></li>)}
          </ul>
          {editable && (
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (ws.trim()) addWs.mutate(); }}>
              <Input aria-label="New workspace" placeholder="New workspace" value={ws} onChange={(e) => setWs(e.target.value)} />
              <Button type="submit" icon={<Plus className="h-4 w-4" />} loading={addWs.isPending} />
            </form>
          )}
          <ErrorBox error={addWs.error} />
        </Card>
        <Card title="Support webhook">
          <p className="mb-2 break-all font-mono text-xs text-slate-700">{org.data!.webhook_url}</p>
          <p className="text-xs text-slate-500">Requests must carry an HMAC-SHA256 signature made with the webhook secret. Rotating invalidates the old secret immediately.</p>
          {editable && (
            <Button className="mt-3" icon={<RotateCw className="h-4 w-4" />} loading={rotate.isPending}
              onClick={() => confirm("Rotate the webhook secret? Existing integrations will stop working until updated.") && rotate.mutate()}>
              Rotate secret
            </Button>
          )}
          {revealed && (
            <div className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800 ring-1 ring-amber-200">
              Copy it now — it will not be shown again.
              <code className="mt-1 block break-all font-mono text-[11px] text-slate-900">{revealed}</code>
            </div>
          )}
          <ErrorBox error={rotate.error} />
        </Card>
      </div>
    </div>
  );
}

function Team() {
  const { can, me: session } = useAuth();
  const me = session?.user;
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const [inviting, setInviting] = useState(false);
  const update = useMutation({
    mutationFn: ({ id, ...body }: { id: string; role?: string; is_active?: boolean }) => api.patch(`/users/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const editable = can("user.write");
  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
      <Card title="Members" actions={editable && <Button size="sm" variant="primary" icon={<UserPlus className="h-3.5 w-3.5" />} onClick={() => setInviting(true)}>Add member</Button>} bodyClass="">
        {users.isLoading ? <Spinner /> : (
          <Table head={["Member", "Role", "Active", "Joined"]}>
            {(users.data ?? []).map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
                <td className="px-3 py-2.5">
                  <div className="font-medium text-slate-900">{u.name}{u.id === me?.id && <span className="ml-1 text-xs text-slate-400">(you)</span>}</div>
                  <div className="text-xs text-slate-500">{u.email}{u.title && ` · ${u.title}`}</div>
                </td>
                <td className="px-3 py-2.5">
                  {editable && u.id !== me?.id ? (
                    <Select aria-label={`Role for ${u.name}`} value={u.role} onChange={(e) => update.mutate({ id: u.id, role: e.target.value })} className="w-44">
                      {ROLES.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
                    </Select>
                  ) : <Badge>{titleCase(u.role)}</Badge>}
                </td>
                <td className="px-3 py-2.5">
                  <Toggle checked={u.is_active} disabled={!editable || u.id === me?.id} onChange={(v) => update.mutate({ id: u.id, is_active: v })} label={`active ${u.name}`} />
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-xs text-slate-500">{timeAgo(u.created_at)}</td>
              </tr>
            ))}
          </Table>
        )}
        <div className="px-4 pb-3"><ErrorBox error={update.error} /></div>
      </Card>
      <Card title="Roles">
        <dl className="space-y-2 text-sm">
          {ROLES.map((r) => (
            <div key={r}>
              <dt className="font-medium text-slate-800">{titleCase(r)}</dt>
              <dd className="text-xs text-slate-500">{ROLE_HINT[r]}</dd>
            </div>
          ))}
        </dl>
      </Card>
      <InviteModal open={inviting} onClose={() => setInviting(false)} />
    </div>
  );
}

function InviteModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const blank = { name: "", email: "", title: "", role: "viewer", password: "" };
  const [d, setD] = useState(blank);
  const create = useMutation({
    mutationFn: () => api.post("/users", d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      setD(blank);
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="Add member"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={create.isPending} onClick={() => create.mutate()}>Create account</Button></>}>
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><Input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></Field>
          <Field label="Email"><Input type="email" value={d.email} onChange={(e) => setD({ ...d, email: e.target.value })} /></Field>
          <Field label="Title"><Input value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} /></Field>
          <Field label="Role" hint={ROLE_HINT[d.role]}>
            <Select value={d.role} onChange={(e) => setD({ ...d, role: e.target.value })}>
              {ROLES.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Initial password" hint="At least 10 characters. Share it securely.">
          <Input type="password" autoComplete="new-password" value={d.password} onChange={(e) => setD({ ...d, password: e.target.value })} />
        </Field>
        <ErrorBox error={create.error} />
      </div>
    </Modal>
  );
}

function Secrets() {
  const qc = useQueryClient();
  const secrets = useQuery({ queryKey: ["secrets"], queryFn: () => api.get<Secret[]>("/secrets") });
  const [d, setD] = useState({ name: "", value: "", description: "" });
  const put = useMutation({
    mutationFn: () => api.put("/secrets", d),
    onSuccess: () => {
      setD({ name: "", value: "", description: "" });
      qc.invalidateQueries({ queryKey: ["secrets"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/secrets/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["secrets"] }),
  });
  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
      <Card title="Secrets" subtitle="Encrypted at rest (Fernet). Values are never returned by the API — only a masked preview." bodyClass="">
        {secrets.isLoading ? <Spinner /> : (secrets.data ?? []).length === 0 ? <Empty title="No secrets stored" icon={<KeyRound className="h-5 w-5" />} /> : (
          <Table head={["Name", "Preview", "Description", "Updated", ""]}>
            {secrets.data!.map((s) => (
              <tr key={s.id}>
                <td className="px-3 py-2.5 font-mono text-xs">{s.name}</td>
                <td className="px-3 py-2.5 font-mono text-xs text-slate-500">{s.preview}</td>
                <td className="px-3 py-2.5 text-xs text-slate-600">{s.description}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-xs text-slate-500">{timeAgo(s.updated_at ?? s.created_at)}</td>
                <td className="px-3 py-2.5 text-right">
                  <Button size="sm" variant="ghost" aria-label={`Delete ${s.name}`} icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => confirm(`Delete secret ${s.name}?`) && remove.mutate(s.id)} />
                </td>
              </tr>
            ))}
          </Table>
        )}
        <div className="px-4 pb-3"><ErrorBox error={remove.error} /></div>
      </Card>
      <Card title="Add or replace a secret">
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); put.mutate(); }}>
          <Field label="Name" hint="Letters, digits, dot, dash and underscore. Writing an existing name replaces it.">
            <Input required pattern="[A-Za-z0-9_.\-]+" placeholder="OPENAI_API_KEY" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} />
          </Field>
          <Field label="Value"><Input required type="password" autoComplete="off" value={d.value} onChange={(e) => setD({ ...d, value: e.target.value })} /></Field>
          <Field label="Description"><Input value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} /></Field>
          <ErrorBox error={put.error} />
          <Button type="submit" variant="primary" className="w-full" loading={put.isPending}>Save secret</Button>
        </form>
      </Card>
    </div>
  );
}

function System() {
  const { can } = useAuth();
  const status = useQuery({ queryKey: ["system"], queryFn: () => api.get<SystemStatus>("/system/status"), refetchInterval: 5_000 });
  const recover = useMutation({ mutationFn: () => api.post<Record<string, unknown>>("/system/recover") });
  const s = status.data;
  if (status.isLoading) return <Spinner />;
  if (status.error || !s) return <ErrorBox error={status.error} />;
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card title="Runtime" actions={<RefreshCw className={`h-4 w-4 text-slate-400 ${status.isFetching ? "animate-spin" : ""}`} />}>
        <KV items={[
          ["Environment", s.environment],
          ["Database", s.database],
          ["Queue", `${s.queue.backend} · ${s.queue.depth} waiting`],
          ["Workers", s.workers.embedded ? `${s.workers.name} · ${s.workers.busy}/${s.workers.concurrency} busy · ${compact(s.workers.processed)} processed` : "external worker processes"],
          ["Active tasks", s.active_tasks],
          ["Embeddings", s.embedding_provider],
          ["Vector store", s.vector_backend],
        ]} />
        {can("settings.write") && (
          <div className="mt-4 border-t border-slate-100 pt-4">
            <Button icon={<RotateCw className="h-4 w-4" />} loading={recover.isPending} onClick={() => recover.mutate()}>Run crash recovery now</Button>
            <p className="mt-1 text-xs text-slate-500">Re-queues tasks whose worker lease expired and resumes them from their last checkpoint. Runs automatically on startup and periodically.</p>
            {recover.data && <pre className="mt-2 rounded bg-slate-50 p-2 font-mono text-[11px]">{JSON.stringify(recover.data)}</pre>}
            <ErrorBox error={recover.error} />
          </div>
        )}
      </Card>
      <Card title="Model providers" subtitle={`Routing order: ${s.provider_order.join(" → ")}`}>
        <Table head={["Provider", "Available", "Circuit breaker"]}>
          {s.providers.map((p) => (
            <tr key={p.provider}>
              <td className="px-3 py-2 font-mono text-xs">{p.provider}</td>
              <td className="px-3 py-2"><Badge className={p.available ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-100 text-slate-500"}>{p.available ? "configured" : "no credentials"}</Badge></td>
              <td className="px-3 py-2"><Badge className={CIRCUIT_STYLE[p.circuit]}>{titleCase(p.circuit)}</Badge></td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Model catalogue" bodyClass="">
        <Table head={["Model", "Provider", "Tier", "Context", "Input / output per 1M tokens"]}>
          {s.models.map((m) => (
            <tr key={`${m.provider}/${m.name}`}>
              <td className="px-3 py-2 font-mono text-xs">{m.name}</td>
              <td className="px-3 py-2 text-xs">{m.provider}</td>
              <td className="px-3 py-2"><Badge>{m.tier}</Badge></td>
              <td className="px-3 py-2 text-xs tabular-nums">{compact(m.context_window)}</td>
              <td className="px-3 py-2 text-xs tabular-nums">{usd(m.input_cost_per_mtok)} / {usd(m.output_cost_per_mtok)}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <div className="space-y-6">
        <Card title="Purpose → tier routing">
          <div className="flex flex-wrap gap-2">
            {Object.entries(s.purpose_tiers).map(([p, t]) => <Badge key={p}>{p} → <span className="font-semibold">{t}</span></Badge>)}
          </div>
        </Card>
        <Card title="Safety limits">
          <KV items={Object.entries(s.limits).map(([k, v]) => [titleCase(k), String(v)])} />
        </Card>
        <Card title="Tasks by status">
          <div className="flex flex-wrap gap-2">
            {Object.entries(s.tasks).map(([k, n]) => <span key={k} className="flex items-center gap-1"><StatusBadge status={k} /><span className="text-xs tabular-nums text-slate-600">{n}</span></span>)}
          </div>
        </Card>
      </div>
    </div>
  );
}
