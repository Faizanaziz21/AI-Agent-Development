import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, Coins, Gauge, Shuffle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Badge, Card, Empty, ErrorBox, PageHeader, ProgressBar, Select, Spinner, Stat, StatusBadge, Table } from "../components/ui";
import { api } from "../lib/api";
import { compact, pct, timeAgo, titleCase, usd } from "../lib/format";
import type { Bucket, CostSummary, ModelUsage } from "../lib/types";

const PALETTE = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#14b8a6", "#ec4899"];
const tooltip = { labelStyle: { fontSize: 12 }, contentStyle: { fontSize: 12, borderRadius: 8 } };

export default function Costs() {
  const [days, setDays] = useState(30);
  const summary = useQuery({ queryKey: ["costs", days], queryFn: () => api.get<CostSummary>(`/costs/summary?days=${days}`), refetchInterval: 15_000 });
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.get<ModelUsage[]>("/costs/usage?limit=100"), refetchInterval: 15_000 });
  const s = summary.data;

  return (
    <div>
      <PageHeader
        title="Cost & usage"
        description="Every model call is metered per organisation, project, agent, task, provider and model. Budgets are enforced before each call; near the limit the gateway downgrades to cheaper tiers, and at the limit work pauses for approval."
        actions={
          <Select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-36">
            {[1, 7, 30, 90].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? "s" : ""}</option>)}
          </Select>
        }
      />
      {summary.isLoading ? <Spinner /> : summary.error ? <ErrorBox error={summary.error} /> : s && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <Stat label="Spend" value={usd(s.totals.cost_usd)} hint={`${compact(s.totals.calls)} model calls`} icon={<Coins className="h-4 w-4" />} tone="brand" />
            <Stat label="Tokens" value={compact(s.totals.input_tokens + s.totals.output_tokens)} hint={`${compact(s.totals.input_tokens)} in · ${compact(s.totals.output_tokens)} out`} icon={<Activity className="h-4 w-4" />} tone="sky" />
            <Stat label="Avg latency" value={`${Math.round(s.totals.avg_latency_ms)} ms`} icon={<Gauge className="h-4 w-4" />} tone="emerald" />
            <Stat label="Fallbacks / downgrades" value={`${s.totals.fallbacks} / ${s.totals.downgrades}`} hint="Provider failover and budget-driven tier changes" icon={<Shuffle className="h-4 w-4" />} tone="amber" />
            <Stat label="Failed calls" value={s.totals.failures} hint={s.totals.calls ? `${pct(s.totals.failures / s.totals.calls, 1)} of calls` : undefined} icon={<AlertTriangle className="h-4 w-4" />} tone="rose" />
          </div>

          <div className="grid gap-6 xl:grid-cols-3">
            <Card title="Daily spend" className="xl:col-span-2">
              <div className="h-64">
                <ResponsiveContainer>
                  <AreaChart data={s.daily} margin={{ left: 0, right: 8, top: 8 }}>
                    <defs>
                      <linearGradient id="costs-spend" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#6366f1" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="day" tick={{ fontSize: 11, fill: "#94a3b8" }} tickFormatter={(d: string) => d.slice(5)} />
                    <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} tickFormatter={(v: number) => `$${v.toFixed(v < 10 ? 2 : 0)}`} width={52} />
                    <Tooltip formatter={(v) => usd(Number(v))} {...tooltip} />
                    <Area type="monotone" dataKey="cost_usd" name="Cost" stroke="#6366f1" strokeWidth={2} fill="url(#costs-spend)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title="Monthly budget" subtitle={`${usd(s.budget.month_spent_usd)} of ${usd(s.budget.monthly_budget_usd)}`}>
              <div className="flex h-64 flex-col justify-center gap-4">
                <div className="text-center">
                  <div className="text-4xl font-semibold tracking-tight text-slate-900">{pct(s.budget.ratio, 1)}</div>
                  <div className="text-xs text-slate-500">of this month's organisation budget</div>
                </div>
                <ProgressBar value={s.budget.ratio} tone={s.budget.ratio >= 0.9 ? "rose" : s.budget.ratio >= 0.7 ? "amber" : "emerald"} className="h-2.5" />
                <p className="text-center text-xs text-slate-500">Change the limit in Settings. Projects also carry their own budgets.</p>
              </div>
            </Card>
          </div>

          <div className="grid gap-6 xl:grid-cols-3">
            <BucketChart title="By agent" data={s.by_agent} />
            <BucketPie title="By provider" data={s.by_provider} />
            <BucketChart title="By purpose" data={s.by_purpose} />
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <Card title="By model" bodyClass="">
              <Table head={["Model", "Calls", "Tokens", "Avg latency", "Cost"]}>
                {s.by_model.map((b) => (
                  <tr key={b.key}>
                    <td className="px-3 py-2 font-mono text-xs">{b.key}</td>
                    <td className="px-3 py-2 text-xs tabular-nums">{compact(b.calls)}</td>
                    <td className="px-3 py-2 text-xs tabular-nums">{compact(b.tokens)}</td>
                    <td className="px-3 py-2 text-xs tabular-nums">{Math.round(b.avg_latency_ms)} ms</td>
                    <td className="px-3 py-2 text-xs tabular-nums">{usd(b.cost_usd, 4)}</td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Card title="Project budgets" bodyClass="">
              {s.by_project.length === 0 ? <Empty title="No project spend in this period" /> : (
                <Table head={["Project", "Status", "Spend", "Budget used"]}>
                  {s.by_project.map((p) => (
                    <tr key={p.id}>
                      <td className="max-w-xs truncate px-3 py-2 text-sm"><Link to={`/projects/${p.id}`} className="hover:text-brand-700">{p.name}</Link></td>
                      <td className="px-3 py-2"><StatusBadge status={p.status} /></td>
                      <td className="px-3 py-2 text-xs tabular-nums">{usd(p.spent_usd, 4)} <span className="text-slate-400">/ {usd(p.budget_usd)}</span></td>
                      <td className="w-40 px-3 py-2">
                        <ProgressBar value={p.ratio ?? 0} tone={(p.ratio ?? 0) >= 0.9 ? "rose" : (p.ratio ?? 0) >= 0.7 ? "amber" : "brand"} />
                        <div className="mt-0.5 text-[11px] text-slate-500">{p.ratio != null ? pct(p.ratio) : "—"}</div>
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>

          <Card title="Recent model calls" bodyClass="">
            {usage.isLoading ? <Spinner /> : (
              <Table head={["When", "Agent", "Purpose", "Provider / model", "Tokens", "Latency", "Cost", ""]}>
                {(usage.data ?? []).map((u) => (
                  <tr key={u.id}>
                    <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-500">{timeAgo(u.created_at)}</td>
                    <td className="px-3 py-2 text-xs">{titleCase(u.agent_key || "system")}</td>
                    <td className="px-3 py-2 text-xs">{u.purpose}</td>
                    <td className="px-3 py-2 font-mono text-[11px]">{u.provider}/{u.model}</td>
                    <td className="px-3 py-2 text-xs tabular-nums">{compact(u.input_tokens)} / {compact(u.output_tokens)}</td>
                    <td className="px-3 py-2 text-xs tabular-nums">{u.latency_ms} ms</td>
                    <td className="px-3 py-2 text-xs tabular-nums">{usd(u.cost_usd, 5)}</td>
                    <td className="px-3 py-2">
                      <div className="flex gap-1">
                        {!u.success && <Badge className="bg-rose-50 text-rose-700 ring-rose-200" title={u.error ?? ""}>failed</Badge>}
                        {u.fallback_from && <Badge className="bg-amber-50 text-amber-700 ring-amber-200" title={`fell back from ${u.fallback_from}`}>fallback</Badge>}
                        {u.downgraded && <Badge className="bg-sky-50 text-sky-700 ring-sky-200">downgraded</Badge>}
                      </div>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function BucketChart({ title, data }: { title: string; data: Bucket[] }) {
  const rows = data.slice(0, 10).map((b) => ({ ...b, label: titleCase(b.key || "system") }));
  return (
    <Card title={title}>
      {rows.length === 0 ? <Empty title="No usage" /> : (
        <div style={{ height: Math.max(160, rows.length * 30) }}>
          <ResponsiveContainer>
            <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11, fill: "#94a3b8" }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
              <YAxis type="category" dataKey="label" width={110} tick={{ fontSize: 11, fill: "#475569" }} />
              <Tooltip formatter={(v) => usd(Number(v), 4)} {...tooltip} />
              <Bar dataKey="cost_usd" name="Cost" fill="#6366f1" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

function BucketPie({ title, data }: { title: string; data: Bucket[] }) {
  return (
    <Card title={title}>
      {data.length === 0 ? <Empty title="No usage" /> : (
        <div className="h-56">
          <ResponsiveContainer>
            <PieChart>
              <Pie data={data} dataKey="calls" nameKey="key" innerRadius={50} outerRadius={80} paddingAngle={2} label={({ name }) => String(name)}>
                {data.map((b, i) => <Cell key={b.key} fill={PALETTE[i % PALETTE.length]} />)}
              </Pie>
              <Tooltip formatter={(v, _n, item) => `${compact(Number(v))} calls · ${usd((item.payload as Bucket).cost_usd, 4)}`} {...tooltip} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}
