import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, CheckSquare, CircleDollarSign, FolderKanban, Plus, Server } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { EventFeed } from "../components/EventFeed";
import { Badge, Button, Card, Empty, PageHeader, ProgressBar, Stat, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { compact, pct, timeAgo, titleCase, usd } from "../lib/format";
import { useEventStream } from "../lib/sse";
import type { CostSummary, Project, SystemStatus } from "../lib/types";
import { NewProjectModal } from "./Projects";

export default function Dashboard() {
  const { me, can } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects"), refetchInterval: 5_000 });
  const costs = useQuery({ queryKey: ["costs", 14], queryFn: () => api.get<CostSummary>("/costs/summary?days=14"), refetchInterval: 15_000 });
  const status = useQuery({ queryKey: ["system", "status"], queryFn: () => api.get<SystemStatus>("/system/status"), refetchInterval: 5_000 });
  const approvals = useQuery({ queryKey: ["approvals", "summary"], queryFn: () => api.get<{ by_status: Record<string, number>; pending_by_risk: Record<string, number> }>("/approvals/summary") });
  const { events, state } = useEventStream("/events/stream", (e) => {
    if (e.type.startsWith("PROJECT_") || e.type === "TASK_COMPLETED") qc.invalidateQueries({ queryKey: ["projects"] });
  }, 300);

  const list = projects.data ?? [];
  const active = list.filter((p) => ["RUNNING", "PLANNING", "WAITING_APPROVAL"].includes(p.status));
  const pending = approvals.data?.by_status?.PENDING ?? 0;
  const highRisk = (approvals.data?.pending_by_risk?.high ?? 0) + (approvals.data?.pending_by_risk?.critical ?? 0);
  const budget = costs.data?.budget;

  return (
    <div>
      <PageHeader
        title={`Good to see you, ${me?.user.name.split(" ")[0] ?? ""}`}
        description="Live overview of your digital workforce: what agents are working on, what needs your decision, and what it costs."
        actions={can("project.write") && <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)}>New project</Button>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Active projects" value={active.length} hint={`${list.length} total`} icon={<FolderKanban className="h-5 w-5" />} tone="brand" />
        <Stat
          label="Pending approvals"
          value={<Link to="/approvals" className="hover:text-brand-700">{pending}</Link>}
          hint={highRisk ? `${highRisk} high risk` : "None high risk"}
          icon={<CheckSquare className="h-5 w-5" />}
          tone={pending ? "amber" : "emerald"}
        />
        <Stat
          label="Spend this month"
          value={usd(budget?.month_spent_usd ?? 0, 2)}
          hint={budget ? `${pct(budget.ratio, 1)} of ${usd(budget.monthly_budget_usd, 0)} budget` : "—"}
          icon={<CircleDollarSign className="h-5 w-5" />}
          tone={(budget?.ratio ?? 0) > 0.8 ? "rose" : "emerald"}
        />
        <Stat
          label="Agents working now"
          value={status.data?.active_tasks ?? "—"}
          hint={status.data ? `${status.data.workers.busy}/${status.data.workers.concurrency} workers busy · queue ${status.data.queue.depth}` : ""}
          icon={<Activity className="h-5 w-5" />}
          tone="sky"
        />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          <Card title="Projects" subtitle="Most recent first" actions={<Link to="/projects" className="text-xs font-medium text-brand-600 hover:underline">View all</Link>} bodyClass="">
            {list.length === 0 ? (
              <Empty title="No projects yet">Start the B2B Sales Intelligence template or submit a demo support ticket to watch the agents work.</Empty>
            ) : (
              <ul className="divide-y divide-slate-100">
                {list.slice(0, 7).map((p) => (
                  <li key={p.id}>
                    <Link to={`/projects/${p.id}`} className="flex items-center gap-4 px-4 py-3 hover:bg-slate-50">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium text-slate-900">{p.name}</span>
                          {(p.pending_approvals ?? 0) > 0 && <Badge className="bg-amber-50 text-amber-700 ring-amber-200">{p.pending_approvals} approval{p.pending_approvals === 1 ? "" : "s"}</Badge>}
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-xs text-slate-500">
                          <span>{titleCase(p.template)}</span>
                          <span>{p.tasks_total} tasks</span>
                          <span>{usd(p.spent_usd)} / {usd(p.budget_usd, 0)}</span>
                          <span>{timeAgo(p.created_at)}</span>
                        </div>
                      </div>
                      <div className="hidden w-40 sm:block">
                        <ProgressBar value={p.progress ?? 0} tone={p.status === "FAILED" ? "rose" : p.status === "COMPLETED" ? "emerald" : "brand"} />
                        <div className="mt-1 text-right text-[11px] text-slate-400">{pct(p.progress)}</div>
                      </div>
                      <StatusBadge status={p.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Model spend — last 14 days" subtitle={costs.data ? `${compact(costs.data.totals.calls)} model calls · ${compact(costs.data.totals.input_tokens + costs.data.totals.output_tokens)} tokens · ${costs.data.totals.fallbacks} fallbacks` : undefined}>
            <div className="h-56">
              <ResponsiveContainer>
                <AreaChart data={costs.data?.daily ?? []} margin={{ left: 0, right: 8, top: 8 }}>
                  <defs>
                    <linearGradient id="spend" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#6366f1" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis dataKey="day" tick={{ fontSize: 11, fill: "#94a3b8" }} tickFormatter={(d: string) => d.slice(5)} />
                  <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} tickFormatter={(v: number) => `$${v.toFixed(v < 10 ? 2 : 0)}`} width={52} />
                  <Tooltip formatter={(v) => usd(Number(v))} labelStyle={{ fontSize: 12 }} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                  <Area type="monotone" dataKey="cost_usd" name="Cost" stroke="#6366f1" strokeWidth={2} fill="url(#spend)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-900">Live activity</h3>
            </div>
            <EventFeed events={events} state={state} height={430} onSelectTask={undefined} />
          </div>
          <Card title="Platform health" actions={<Server className="h-4 w-4 text-slate-400" />}>
            <ul className="space-y-2 text-sm">
              {status.data?.providers.map((p) => (
                <li key={p.provider} className="flex items-center justify-between">
                  <span className="font-medium text-slate-700">{p.provider}</span>
                  <span className="flex items-center gap-2">
                    {!p.available && <Badge>no credentials</Badge>}
                    <Badge className={p.circuit === "closed" ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : p.circuit === "open" ? "bg-rose-50 text-rose-700 ring-rose-200" : "bg-amber-50 text-amber-700 ring-amber-200"}>
                      circuit {p.circuit}
                    </Badge>
                  </span>
                </li>
              ))}
              {status.data && (
                <>
                  <li className="flex justify-between text-slate-500"><span>Queue</span><span>{status.data.queue.backend} · depth {status.data.queue.depth}</span></li>
                  <li className="flex justify-between text-slate-500"><span>Database</span><span>{status.data.database}</span></li>
                  <li className="flex justify-between text-slate-500"><span>Vector search</span><span>{status.data.vector_backend}</span></li>
                </>
              )}
            </ul>
          </Card>
        </div>
      </div>

      <NewProjectModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(p) => {
          setCreating(false);
          navigate(`/projects/${p.id}`);
        }}
      />
    </div>
  );
}
