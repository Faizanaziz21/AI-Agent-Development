import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Ban, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { AgentGraph, EdgeLegend, TaskDag } from "../components/DagView";
import { Deliverable } from "../components/Deliverable";
import { EventFeed, EventRow } from "../components/EventFeed";
import { TaskDrawer } from "../components/TaskDrawer";
import { Badge, Button, Card, Empty, ErrorBox, PageHeader, ProgressBar, RiskBadge, Spinner, Stat, StatusBadge, Table, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { clock, compact, duration, pct, timeAgo, titleCase, usd } from "../lib/format";
import { replayState } from "../lib/replay";
import { useEventStream } from "../lib/sse";
import type { Approval, EventItem, OutboundMessage, ProjectDetail as PD, ProjectGraph, Task } from "../lib/types";

type TabKey = "monitor" | "agents" | "replay" | "deliverable" | "approvals" | "outbox" | "costs";

function Replay({ graph, events, onSelect }: { graph: ProjectGraph; events: EventItem[]; onSelect: (id: string) => void }) {
  const [pos, setPos] = useState(events.length - 1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(8);
  const timer = useRef<number | null>(null);

  useEffect(() => setPos((p) => (p < 0 ? events.length - 1 : p)), [events.length]);
  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      setPos((p) => {
        if (p >= events.length - 1) {
          setPlaying(false);
          return p;
        }
        return p + 1;
      });
    }, 1000 / speed);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing, speed, events.length]);

  const frame = useMemo(() => replayState(events, pos), [events, pos]);
  const nodes = graph.nodes
    .filter((n) => frame.statuses.has(n.id))
    .map((n) => ({ ...n, status: frame.statuses.get(n.id)!, current_action: frame.actions.get(n.id) ?? "", output_summary: frame.statuses.get(n.id) === "COMPLETED" ? n.output_summary : "" }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const current = events[pos];
  const recent = events.slice(Math.max(0, pos - 7), pos + 1).reverse();

  if (events.length === 0) return <Empty title="Nothing to replay yet" />;
  return (
    <div className="space-y-4">
      <Card bodyClass="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" icon={<SkipBack className="h-3.5 w-3.5" />} onClick={() => { setPlaying(false); setPos(0); }} aria-label="Go to start" />
          <Button size="sm" variant="primary" icon={playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />} onClick={() => { if (pos >= events.length - 1) setPos(0); setPlaying(!playing); }}>
            {playing ? "Pause" : "Play"}
          </Button>
          <Button size="sm" icon={<SkipForward className="h-3.5 w-3.5" />} onClick={() => { setPlaying(false); setPos(events.length - 1); }} aria-label="Go to end" />
          <input type="range" min={0} max={events.length - 1} value={pos} onChange={(e) => { setPlaying(false); setPos(Number(e.target.value)); }} className="min-w-48 flex-1 accent-brand-600" aria-label="Replay position" />
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="rounded-md border-slate-300 text-xs ring-1 ring-slate-300">
            {[2, 8, 25, 60].map((s) => <option key={s} value={s}>{s} ev/s</option>)}
          </select>
          <span className="font-mono text-xs text-slate-500">{pos + 1}/{events.length} · {clock(current?.created_at)}</span>
          <StatusBadge status={frame.projectStatus} />
        </div>
        {current && <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700"><b className="mr-2 text-xs uppercase text-slate-500">{titleCase(current.type)}</b>{current.message}</div>}
      </Card>
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <TaskDag nodes={nodes} edges={edges} onSelect={onSelect} height={560} />
        <Card title="Recent events" bodyClass="">
          <ul className="divide-y divide-slate-50">{recent.map((e) => <EventRow key={e.id} e={e} onSelectTask={onSelect} />)}</ul>
        </Card>
      </div>
    </div>
  );
}

function Costs({ tasks }: { tasks: Task[] }) {
  const byAgent = Object.values(
    tasks.reduce<Record<string, { agent: string; cost: number; tokens: number; tasks: number }>>((acc, t) => {
      const k = t.agent_key;
      acc[k] ??= { agent: k, cost: 0, tokens: 0, tasks: 0 };
      acc[k].cost += t.spent_usd;
      acc[k].tokens += t.tokens_used;
      acc[k].tasks += 1;
      return acc;
    }, {}),
  ).sort((a, b) => b.cost - a.cost);
  const top = [...tasks].sort((a, b) => b.spent_usd - a.spent_usd).slice(0, 12);
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card title="Spend by agent">
        <div className="h-72">
          <ResponsiveContainer>
            <BarChart data={byAgent} layout="vertical" margin={{ left: 30 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
              <YAxis type="category" dataKey="agent" tick={{ fontSize: 11 }} width={110} />
              <Tooltip formatter={(v) => usd(Number(v))} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Bar dataKey="cost" name="Cost" fill="#6366f1" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <Card title="Most expensive tasks" bodyClass="">
        <Table head={["Task", "Agent", "Tokens", "Cost"]}>
          {top.map((t) => (
            <tr key={t.id}>
              <td className="max-w-xs truncate px-3 py-1.5 text-xs">{t.title}</td>
              <td className="px-3 py-1.5 text-xs text-slate-500">{t.agent_key}</td>
              <td className="px-3 py-1.5 text-xs">{compact(t.tokens_used)}</td>
              <td className="px-3 py-1.5 text-xs font-medium">{usd(t.spent_usd)}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

export default function ProjectDetail() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabKey>("monitor");
  const [selected, setSelected] = useState<string | null>(null);

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.get<PD>(`/projects/${id}`), refetchInterval: 4_000 });
  const graph = useQuery({ queryKey: ["graph", id], queryFn: () => api.get<ProjectGraph>(`/projects/${id}/graph`), refetchInterval: 6_000 });
  const replay = useQuery({ queryKey: ["replay", id], queryFn: () => api.get<{ events: EventItem[]; tasks: Task[] }>(`/projects/${id}/replay`), enabled: tab === "replay" || tab === "costs" });
  const approvals = useQuery({ queryKey: ["approvals", "project", id], queryFn: () => api.get<Approval[]>(`/approvals?status=ALL&project_id=${id}`), enabled: tab === "approvals" || tab === "monitor", refetchInterval: 5_000 });
  const outbox = useQuery({ queryKey: ["outbox", id], queryFn: () => api.get<OutboundMessage[]>(`/outbox?project_id=${id}`), enabled: tab === "outbox" });

  const pendingRefresh = useRef<number | null>(null);
  const { events, state } = useEventStream(`/projects/${id}/stream`, () => {
    if (pendingRefresh.current) return;
    pendingRefresh.current = window.setTimeout(() => {
      pendingRefresh.current = null;
      qc.invalidateQueries({ queryKey: ["graph", id] });
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["approvals", "project", id] });
    }, 400);
  });

  const cancel = useMutation({
    mutationFn: () => api.post(`/projects/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const p = project.data;
  if (project.error) return <ErrorBox error={project.error} />;
  if (!p) return <Spinner />;
  const active = ["PLANNING", "RUNNING", "WAITING_APPROVAL"].includes(p.status);
  const pending = (approvals.data ?? []).filter((a) => a.status === "PENDING");
  const counts = p.task_counts ?? {};
  const budgetRatio = p.budget_usd ? p.spent_usd / p.budget_usd : 0;

  return (
    <div>
      <Link to="/projects" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-3.5 w-3.5" /> Projects
      </Link>
      <PageHeader
        title={p.name}
        description={p.objective?.text}
        actions={
          <>
            <StatusBadge status={p.status} className="text-sm" />
            {active && can("project.write") && (
              <Button variant="danger" size="sm" icon={<Ban className="h-3.5 w-3.5" />} loading={cancel.isPending} onClick={() => confirm("Cancel this project and all running tasks?") && cancel.mutate()}>
                Cancel project
              </Button>
            )}
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="Progress" value={pct(p.progress)} hint={`${counts.COMPLETED ?? 0} of ${p.tasks_total} tasks complete`} />
        <Stat label="Running now" value={(counts.RUNNING ?? 0) + (counts.IN_REVIEW ?? 0)} hint={`${counts.QUEUED ?? 0} queued · ${counts.PENDING ?? 0} waiting on dependencies`} />
        <Stat label="Needs a human" value={pending.length} hint={pending.length ? <Link className="text-amber-700 hover:underline" to="/approvals">Open approval inbox →</Link> : "Nothing pending"} tone={pending.length ? "amber" : "slate"} />
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="text-xs font-medium text-slate-500">Budget</div>
          <div className="mt-0.5 text-xl font-semibold text-slate-900">{usd(p.spent_usd)} <span className="text-sm font-normal text-slate-400">/ {usd(p.budget_usd, 0)}</span></div>
          <ProgressBar className="mt-2" value={budgetRatio} tone={budgetRatio >= 1 ? "rose" : budgetRatio >= 0.8 ? "amber" : "emerald"} />
          <div className="mt-1 text-xs text-slate-500">{compact(p.tokens_used)} tokens</div>
        </div>
        <Stat label="Elapsed" value={duration(p.started_at, p.completed_at)} hint={`Started ${timeAgo(p.started_at)}`} />
      </div>

      {p.plan?.summary && (
        <div className="mt-4 rounded-xl border border-violet-200 bg-violet-50/50 px-4 py-3 text-sm text-violet-900">
          <span className="mr-2 font-semibold">Supervisor plan v{p.plan.version}:</span>
          {p.plan.summary}
        </div>
      )}
      {Object.keys(p.chaos ?? {}).length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-orange-700">
          <span className="font-semibold">Fault injection active:</span>
          {Object.entries(p.chaos).map(([k, v]) => <Badge key={k} className="bg-orange-50 text-orange-700 ring-orange-200">{titleCase(k)} {pct(v)}</Badge>)}
        </div>
      )}

      <Tabs
        className="mt-6"
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "monitor", label: "Execution monitor" },
          { key: "agents", label: "Agent collaboration" },
          { key: "replay", label: "Replay" },
          { key: "deliverable", label: "Deliverable" },
          { key: "approvals", label: "Approvals", count: approvals.data?.length },
          { key: "outbox", label: "Outbound messages" },
          { key: "costs", label: "Costs" },
        ]}
      />

      <div className="mt-4">
        {tab === "monitor" && (
          <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <EdgeLegend />
                <span className="text-xs text-slate-500">Click a task to inspect its reasoning, tool calls and QA.</span>
              </div>
              {graph.data ? <TaskDag nodes={graph.data.nodes} edges={graph.data.edges} selected={selected} onSelect={setSelected} height={640} /> : <Spinner />}
            </div>
            <div className="space-y-3">
              {pending.length > 0 && (
                <Card title="Waiting for your decision" bodyClass="divide-y divide-slate-100">
                  {pending.map((a) => (
                    <Link key={a.id} to={`/approvals/${a.id}`} className="flex items-center justify-between gap-2 px-4 py-2 hover:bg-amber-50/50">
                      <span className="truncate text-sm">{a.title}</span>
                      <RiskBadge level={a.risk_level} />
                    </Link>
                  ))}
                </Card>
              )}
              <EventFeed events={events} state={state} onSelectTask={setSelected} height={pending.length ? 500 : 640} />
            </div>
          </div>
        )}

        {tab === "agents" && (graph.data ? (
          <div className="space-y-2">
            <p className="text-sm text-slate-500">How work flowed between agents: planning hand-offs, dependencies, delegations and QA reviews. Pulsing agents are working right now.</p>
            <AgentGraph nodes={graph.data.agents.nodes} edges={graph.data.agents.edges} height={560} />
          </div>
        ) : <Spinner />)}

        {tab === "replay" && (replay.data && graph.data ? <Replay graph={graph.data} events={replay.data.events} onSelect={setSelected} /> : <Spinner />)}

        {tab === "deliverable" && (
          <Card title="Final deliverable" subtitle={p.completed_at ? `Delivered ${timeAgo(p.completed_at)}` : "Assembled when the final task completes"}>
            {p.deliverable ? <Deliverable data={p.deliverable} /> : <Empty title="Not ready yet">{active ? "Agents are still working on this project." : p.error ?? "This project ended without a deliverable."}</Empty>}
          </Card>
        )}

        {tab === "approvals" && (
          <Card bodyClass="">
            {(approvals.data ?? []).length === 0 ? <Empty title="No approvals requested in this project" /> : (
              <Table head={["Request", "Agent", "Risk", "Required role", "Status", "Decided", ""]}>
                {approvals.data!.map((a) => (
                  <tr key={a.id}>
                    <td className="max-w-md px-3 py-2"><div className="truncate font-medium">{a.title}</div><div className="truncate text-xs text-slate-500">{a.reason}</div></td>
                    <td className="px-3 py-2 text-xs">{a.agent_key}</td>
                    <td className="px-3 py-2"><RiskBadge level={a.risk_level} /></td>
                    <td className="px-3 py-2 text-xs">{titleCase(a.required_role)}</td>
                    <td className="px-3 py-2"><StatusBadge status={a.status} /></td>
                    <td className="px-3 py-2 text-xs text-slate-500">{a.decided_at ? timeAgo(a.decided_at) : "—"}</td>
                    <td className="px-3 py-2 text-right"><Link className="text-xs font-medium text-brand-600 hover:underline" to={`/approvals/${a.id}`}>Open</Link></td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        )}

        {tab === "outbox" && (
          <Card bodyClass="" subtitle="Messages are drafted by agents and only sent after a human approves them.">
            {(outbox.data ?? []).length === 0 ? <Empty title="No outbound messages" /> : (
              <Table head={["Recipient", "Subject", "Channel", "Status", "Created"]}>
                {outbox.data!.map((m) => (
                  <tr key={m.id} className="align-top">
                    <td className="px-3 py-2 text-xs font-medium">{m.recipient}</td>
                    <td className="max-w-lg px-3 py-2 text-xs">
                      <details>
                        <summary className="cursor-pointer">{m.subject || "(no subject)"}</summary>
                        <pre className="mt-2 whitespace-pre-wrap rounded-md bg-slate-50 p-2 font-sans text-xs text-slate-700">{m.body}</pre>
                      </details>
                    </td>
                    <td className="px-3 py-2 text-xs">{m.channel}</td>
                    <td className="px-3 py-2"><Badge className={clsx(m.status === "sent" ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-50 text-slate-600 ring-slate-200")}>{m.status}</Badge></td>
                    <td className="px-3 py-2 text-xs text-slate-500">{timeAgo(m.created_at)}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        )}

        {tab === "costs" && (replay.data ? <Costs tasks={replay.data.tasks} /> : <Spinner />)}
      </div>

      <TaskDrawer taskId={selected} onClose={() => setSelected(null)} onSelect={setSelected} />
    </div>
  );
}
