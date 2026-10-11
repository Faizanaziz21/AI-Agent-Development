import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, PageHeader, ProgressBar, Select, Spinner, StatusBadge, Table, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { duration, pct, timeAgo, titleCase, usd } from "../lib/format";
import type { Project, Templates } from "../lib/types";

const CHAOS_LABELS: Record<string, string> = {
  provider_failure_rate: "Model provider outage",
  malformed_output_rate: "Malformed model output",
  invalid_response_rate: "Invalid model response",
  tool_failure_rate: "Tool failure",
  tool_timeout_rate: "Tool timeout",
  worker_crash_rate: "Worker crash",
};

export function NewProjectModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (p: Project) => void }) {
  const qc = useQueryClient();
  const { data: tpl } = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Templates>("/projects/templates"), enabled: open, staleTime: 300_000 });
  const [template, setTemplate] = useState("b2b_sales");
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [budget, setBudget] = useState(50);
  const [showChaos, setShowChaos] = useState(false);
  const [chaos, setChaos] = useState<Record<string, number>>({});

  const current = tpl?.templates.find((t) => t.key === template);
  useEffect(() => {
    if (!current) return;
    setName(current.key === "generic" ? "" : current.name);
    setObjective(current.objective);
    setBudget(current.budget_usd);
  }, [current]);

  const create = useMutation({
    mutationFn: () =>
      api.post<Project>("/projects", {
        name,
        objective,
        template,
        budget_usd: budget,
        chaos: Object.fromEntries(Object.entries(chaos).filter(([, v]) => v > 0)),
      }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      onCreated(p);
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Start a new project"
      wide
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={create.isPending} disabled={!name || objective.length < 10} onClick={() => create.mutate()}>
            Plan & start
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-2">
          {tpl?.templates.map((t) => (
            <button
              key={t.key}
              onClick={() => setTemplate(t.key)}
              className={`rounded-lg p-3 text-left ring-1 transition ${template === t.key ? "bg-brand-50 ring-2 ring-brand-500" : "ring-slate-200 hover:bg-slate-50"}`}
            >
              <div className="text-sm font-medium text-slate-900">{t.name}</div>
              <div className="mt-0.5 line-clamp-2 text-xs text-slate-500">{t.objective || "Describe any business objective; the Supervisor plans the work."}</div>
            </button>
          ))}
        </div>
        <Field label="Project name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Q3 healthcare pipeline" />
        </Field>
        <Field label="Business objective" hint="The Supervisor Agent turns this into a dependency graph of tasks for specialist agents.">
          <Textarea rows={4} value={objective} onChange={(e) => setObjective(e.target.value)} />
        </Field>
        <Field label="Budget (USD)" hint="Agents downgrade to cheaper models at 80% and pause for approval at 100%.">
          <Input type="number" min={1} step={1} value={budget} onChange={(e) => setBudget(Number(e.target.value))} className="max-w-40" />
        </Field>
        <div className="rounded-lg border border-dashed border-slate-300 p-3">
          <button className="flex items-center gap-2 text-sm font-medium text-slate-700" onClick={() => setShowChaos((s) => !s)}>
            <FlaskConical className="h-4 w-4 text-orange-500" /> Fault injection (chaos testing) {showChaos ? "▾" : "▸"}
          </button>
          {showChaos && (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {(tpl?.chaos_keys ?? []).map((k) => (
                <Field key={k} label={`${CHAOS_LABELS[k] ?? titleCase(k)} — ${pct(chaos[k] ?? 0)}`}>
                  <input type="range" min={0} max={0.5} step={0.05} value={chaos[k] ?? 0} onChange={(e) => setChaos({ ...chaos, [k]: Number(e.target.value) })} className="w-full accent-orange-500" />
                </Field>
              ))}
              <p className="text-xs text-slate-500 sm:col-span-2">Failures are injected per call so you can watch retries, provider fallback, malformed-output repair and crash recovery in the execution monitor.</p>
            </div>
          )}
        </div>
        <ErrorBox error={create.error} />
      </div>
    </Modal>
  );
}

export default function Projects() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects"), refetchInterval: 4_000 });
  const rows = useMemo(
    () => (data ?? []).filter((p) => (!status || p.status === status) && (!q || p.name.toLowerCase().includes(q.toLowerCase()))),
    [data, status, q],
  );

  return (
    <div>
      <PageHeader
        title="Projects"
        description="Each project is a business objective decomposed into a task graph and executed by a team of agents."
        actions={can("project.write") && <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)}>New project</Button>}
      />
      <Card bodyClass="">
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search projects" className="w-64 pl-8" />
          </div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-48">
            <option value="">All statuses</option>
            {["PLANNING", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED"].map((s) => (
              <option key={s} value={s}>{titleCase(s)}</option>
            ))}
          </Select>
          <span className="ml-auto text-xs text-slate-500">{rows.length} projects</span>
        </div>
        {isLoading ? (
          <Spinner />
        ) : rows.length === 0 ? (
          <Empty title="No matching projects" />
        ) : (
          <Table head={["Project", "Status", "Progress", "Tasks", "Approvals", "Spend", "Duration", "Created"]}>
            {rows.map((p) => (
              <tr key={p.id} className="cursor-pointer hover:bg-slate-50" onClick={() => navigate(`/projects/${p.id}`)}>
                <td className="max-w-sm px-3 py-2.5">
                  <Link to={`/projects/${p.id}`} className="block truncate font-medium text-slate-900 hover:text-brand-700">{p.name}</Link>
                  <div className="text-xs text-slate-500">
                    {titleCase(p.template)}
                    {Object.keys(p.chaos ?? {}).length > 0 && <Badge className="ml-2 bg-orange-50 text-orange-700 ring-orange-200">chaos</Badge>}
                  </div>
                </td>
                <td className="px-3 py-2.5"><StatusBadge status={p.status} /></td>
                <td className="w-40 px-3 py-2.5">
                  <ProgressBar value={p.progress ?? 0} tone={p.status === "COMPLETED" ? "emerald" : p.status === "FAILED" ? "rose" : "brand"} />
                  <div className="mt-1 text-[11px] text-slate-400">{pct(p.progress)}</div>
                </td>
                <td className="px-3 py-2.5 text-slate-600">{p.tasks_total}</td>
                <td className="px-3 py-2.5">{(p.pending_approvals ?? 0) > 0 ? <Badge className="bg-amber-50 text-amber-700 ring-amber-200">{p.pending_approvals} pending</Badge> : <span className="text-slate-400">—</span>}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-slate-600">
                  {usd(p.spent_usd)} <span className="text-slate-400">/ {usd(p.budget_usd, 0)}</span>
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-slate-600">{duration(p.started_at, p.completed_at)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-slate-500">{timeAgo(p.created_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <NewProjectModal open={creating} onClose={() => setCreating(false)} onCreated={(p) => navigate(`/projects/${p.id}`)} />
    </div>
  );
}
