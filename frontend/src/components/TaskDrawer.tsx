import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Ban, ExternalLink, RotateCcw, ShieldAlert, UserCog } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { clock, duration, pct, titleCase, usd } from "../lib/format";
import type { AgentDefinition, TaskDetail } from "../lib/types";
import { Badge, Button, Drawer, Empty, ErrorBox, JsonView, KV, RiskBadge, Select, Spinner, StatusBadge, Table, Tabs } from "./ui";

type TabKey = "overview" | "steps" | "tools" | "qa" | "models" | "approvals";

const PHASE_TONE: Record<string, string> = {
  THINK: "bg-violet-50 text-violet-700 ring-violet-200",
  PLAN: "bg-violet-50 text-violet-700 ring-violet-200",
  SELECT_TOOL: "bg-sky-50 text-sky-700 ring-sky-200",
  EXECUTE_TOOL: "bg-blue-50 text-blue-700 ring-blue-200",
  OBSERVE: "bg-slate-50 text-slate-700 ring-slate-200",
  EVALUATE: "bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200",
  COMPLETE: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  RETRY: "bg-orange-50 text-orange-700 ring-orange-200",
  ESCALATE: "bg-rose-50 text-rose-700 ring-rose-200",
  CRASH: "bg-rose-50 text-rose-700 ring-rose-200",
};

export function TaskDrawer({ taskId, onClose, onSelect }: { taskId: string | null; onClose: () => void; onSelect: (id: string) => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState<TabKey>("overview");
  const [reassignTo, setReassignTo] = useState("");
  const { data, isLoading, error } = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.get<TaskDetail>(`/tasks/${taskId}`),
    enabled: !!taskId,
    refetchInterval: (q) => (q.state.data && ["COMPLETED", "FAILED", "CANCELLED", "SKIPPED"].includes(q.state.data.task.status) ? false : 2_000),
  });
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api.get<AgentDefinition[]>("/agents"), enabled: !!taskId && can("project.write") });
  const act = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: unknown }) => api.post(`/tasks/${taskId}/${action}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      qc.invalidateQueries({ queryKey: ["graph"] });
    },
  });

  const t = data?.task;
  const steps = data?.executions.flatMap((e) => e.steps.map((s) => ({ ...s, attempt: e.attempt }))) ?? [];
  const terminal = t && ["COMPLETED", "FAILED", "CANCELLED", "SKIPPED"].includes(t.status);

  return (
    <Drawer
      open={!!taskId}
      onClose={onClose}
      title={t?.title ?? "Task"}
      subtitle={
        t && (
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={t.status} />
            <span>{data?.agent?.name ?? t.agent_key}</span>
            <span className="font-mono text-slate-400">{t.key}</span>
            <span>attempt {t.attempt}/{t.max_attempts}</span>
            {t.revision > 0 && <span>revision {t.revision}</span>}
            {t.delegation_depth > 0 && <span>depth {t.delegation_depth}</span>}
          </span>
        )
      }
      actions={
        t &&
        can("project.write") && (
          <>
            {["FAILED", "ESCALATED", "CANCELLED"].includes(t.status) && (
              <Button size="sm" icon={<RotateCcw className="h-3.5 w-3.5" />} loading={act.isPending} onClick={() => act.mutate({ action: "retry" })}>
                Retry
              </Button>
            )}
            {!terminal && (
              <Button size="sm" variant="ghost" icon={<Ban className="h-3.5 w-3.5" />} loading={act.isPending} onClick={() => act.mutate({ action: "cancel" })}>
                Cancel
              </Button>
            )}
          </>
        )
      }
    >
      {isLoading || !data || !t ? (
        error ? <div className="p-5"><ErrorBox error={error} /></div> : <Spinner />
      ) : (
        <div>
          <Tabs
            className="sticky top-0 z-10 bg-white px-5"
            value={tab}
            onChange={setTab}
            tabs={[
              { key: "overview", label: "Overview" },
              { key: "steps", label: "Reasoning steps", count: steps.length },
              { key: "tools", label: "Tool calls", count: data.tool_calls.length },
              { key: "qa", label: "QA", count: data.evaluations.length },
              { key: "models", label: "Model calls", count: data.model_usage.length },
              { key: "approvals", label: "Approvals", count: data.approvals.length },
            ]}
          />
          <div className="space-y-5 p-5">
            <ErrorBox error={act.error} />
            {tab === "overview" && (
              <>
                {t.error && (
                  <div className="flex gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    {t.error}
                  </div>
                )}
                <KV
                  items={[
                    ["Capability", <span className="font-mono text-xs">{t.capability}</span>],
                    ["Agent instance", t.agent_instance || t.agent_key],
                    ["Current action", t.current_action || "—"],
                    ["Result", t.output_summary || "—"],
                    ["Quality score", t.quality_score !== null ? pct(t.quality_score) : t.requires_review ? "awaiting review" : "not reviewed"],
                    ["Spend", `${usd(t.spent_usd)} of ${usd(t.budget_usd, 2)} · ${t.tokens_used.toLocaleString()} tokens`],
                    ["Tool calls", `${t.tool_calls} (${t.failures} failures, ${t.retries} retries)`],
                    ["Duration", duration(t.started_at, t.completed_at)],
                  ]}
                />
                {t.feedback?.length > 0 && (
                  <div>
                    <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Reviewer & human feedback</h4>
                    <ul className="space-y-1.5">
                      {t.feedback.map((f, i) => (
                        <li key={i} className="rounded-lg bg-orange-50 px-3 py-2 text-sm text-orange-800">{f}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {[["Depends on", data.dependencies], ["Delegated sub-tasks", data.children], ["QA reviews", data.reviews]].map(([label, list]) =>
                  (list as typeof data.children).length ? (
                    <div key={label as string}>
                      <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">{label as string}</h4>
                      <ul className="divide-y divide-slate-100 rounded-lg ring-1 ring-slate-200">
                        {(list as typeof data.children).map((c) => (
                          <li key={c.id}>
                            <button className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-slate-50" onClick={() => onSelect(c.id)}>
                              <span className="truncate">{c.title}</span>
                              <StatusBadge status={c.status} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null,
                )}
                <div>
                  <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Input</h4>
                  <JsonView value={t.input} collapsed />
                </div>
                <div>
                  <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Output</h4>
                  {t.output ? <JsonView value={t.output} collapsed={JSON.stringify(t.output).length > 1500} /> : <span className="text-sm text-slate-400">No output yet</span>}
                </div>
                {can("project.write") && !terminal && (
                  <div className="flex items-end gap-2 rounded-lg bg-slate-50 p-3">
                    <UserCog className="mb-2 h-4 w-4 text-slate-400" />
                    <label className="flex-1">
                      <span className="mb-1 block text-xs font-medium text-slate-600">Reassign to another agent</span>
                      <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)}>
                        <option value="">Choose agent…</option>
                        {agents.data?.filter((a) => a.enabled && a.key !== t.agent_key).map((a) => (
                          <option key={a.key} value={a.key}>{a.name}</option>
                        ))}
                      </Select>
                    </label>
                    <Button disabled={!reassignTo} loading={act.isPending} onClick={() => act.mutate({ action: "reassign", body: { agent_key: reassignTo } })}>
                      Reassign
                    </Button>
                  </div>
                )}
              </>
            )}

            {tab === "steps" &&
              (steps.length === 0 ? (
                <Empty title="No steps recorded yet" />
              ) : (
                <ol className="relative space-y-3 border-l-2 border-slate-100 pl-5">
                  {steps.map((s) => (
                    <li key={s.id} className="relative">
                      <span className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-white bg-slate-300 ring-1 ring-slate-200" />
                      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                        <span className="font-mono">{clock(s.created_at)}</span>
                        <Badge className={PHASE_TONE[s.phase] ?? undefined}>{titleCase(s.phase)}</Badge>
                        {s.action && <span className="font-mono text-slate-500">{s.action}</span>}
                        {s.tool_name && <Badge className="bg-blue-50 text-blue-700 ring-blue-200">{s.tool_name}</Badge>}
                        {s.confidence !== null && <span>confidence {pct(s.confidence)}</span>}
                        <span>attempt {s.attempt}</span>
                        {s.flags?.map((f) => (
                          <Badge key={f} className="bg-rose-50 text-rose-700 ring-rose-200"><ShieldAlert className="h-3 w-3" />{f}</Badge>
                        ))}
                      </div>
                      {s.reason_summary && <p className="mt-1 text-sm text-slate-700">{s.reason_summary}</p>}
                      {s.output_summary && <p className="mt-0.5 text-sm text-slate-500">→ {s.output_summary}</p>}
                    </li>
                  ))}
                </ol>
              ))}

            {tab === "tools" &&
              (data.tool_calls.length === 0 ? (
                <Empty title="No tool calls" />
              ) : (
                <div className="space-y-3">
                  {data.tool_calls.map((c) => (
                    <details key={c.id} className="group rounded-lg ring-1 ring-slate-200">
                      <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2 text-sm">
                        <span className="font-mono font-medium text-slate-800">{c.tool_name}</span>
                        <StatusBadge status={c.status} />
                        {c.policy_decision?.effect && c.policy_decision.effect !== "allow" && <Badge>{titleCase(c.policy_decision.effect)}</Badge>}
                        {c.policy_decision?.flags?.some((f) => f.startsWith("injection")) && (
                          <Badge className="bg-rose-50 text-rose-700 ring-rose-200"><ShieldAlert className="h-3 w-3" />injection neutralised</Badge>
                        )}
                        <span className="ml-auto text-xs text-slate-400">{c.duration_ms} ms · {c.attempts} attempt{c.attempts === 1 ? "" : "s"}</span>
                      </summary>
                      <div className="space-y-3 border-t border-slate-100 p-3">
                        {c.error && <ErrorBox error={c.error} />}
                        {(c.policy_decision?.matched?.length ?? 0) > 0 && (
                          <div className="text-xs text-slate-600">
                            Policies: {c.policy_decision.matched!.map((m) => `${m.name} (${m.effect})`).join(", ")}
                          </div>
                        )}
                        <div>
                          <div className="mb-1 text-xs font-semibold text-slate-500">Arguments</div>
                          <JsonView value={c.arguments} />
                        </div>
                        <div>
                          <div className="mb-1 text-xs font-semibold text-slate-500">Result</div>
                          <JsonView value={c.result} collapsed={JSON.stringify(c.result ?? "").length > 1200} />
                        </div>
                      </div>
                    </details>
                  ))}
                </div>
              ))}

            {tab === "qa" &&
              (data.evaluations.length === 0 ? (
                <Empty title="No evaluations">{t.requires_review ? "The QA Critic reviews this task once it produces output." : "This task does not require QA review."}</Empty>
              ) : (
                <div className="space-y-3">
                  {data.evaluations.map((e) => (
                    <div key={e.id} className={clsx("rounded-lg p-3 ring-1", e.passed ? "bg-emerald-50/40 ring-emerald-200" : "bg-orange-50/40 ring-orange-200")}>
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">Revision {e.revision} · {e.evaluator_agent}</span>
                        <span className={clsx("text-sm font-semibold", e.passed ? "text-emerald-700" : "text-orange-700")}>
                          {pct(e.overall)} {e.passed ? "passed" : `below ${pct(e.threshold)} threshold`}
                        </span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
                        {Object.entries(e.scores).map(([k, v]) => (
                          <div key={k} className="flex justify-between text-xs">
                            <span className="text-slate-500">{titleCase(k)}</span>
                            <span className="font-medium">{pct(v)}</span>
                          </div>
                        ))}
                      </div>
                      {e.feedback && <p className="mt-2 text-sm text-slate-700">{e.feedback}</p>}
                      {e.issues?.length > 0 && (
                        <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
                          {e.issues.map((x, i) => <li key={i}>{x}</li>)}
                        </ul>
                      )}
                    </div>
                  ))}
                </div>
              ))}

            {tab === "models" &&
              (data.model_usage.length === 0 ? (
                <Empty title="No model calls" />
              ) : (
                <Table head={["Time", "Provider / model", "Purpose", "Tokens", "Latency", "Cost", ""]}>
                  {data.model_usage.map((u) => (
                    <tr key={u.id}>
                      <td className="px-3 py-1.5 font-mono text-xs text-slate-500">{clock(u.created_at)}</td>
                      <td className="px-3 py-1.5 text-xs"><span className="font-medium">{u.provider}</span> / {u.model}</td>
                      <td className="px-3 py-1.5 text-xs">{u.purpose}</td>
                      <td className="px-3 py-1.5 text-xs">{(u.input_tokens + u.output_tokens).toLocaleString()}</td>
                      <td className="px-3 py-1.5 text-xs">{u.latency_ms} ms</td>
                      <td className="px-3 py-1.5 text-xs">{usd(u.cost_usd)}</td>
                      <td className="px-3 py-1.5 text-xs">
                        {!u.success && <Badge className="bg-rose-50 text-rose-700 ring-rose-200" title={u.error ?? ""}>failed</Badge>}
                        {u.fallback_from && <Badge className="bg-orange-50 text-orange-700 ring-orange-200">fallback from {u.fallback_from}</Badge>}
                        {u.downgraded && <Badge className="bg-amber-50 text-amber-700 ring-amber-200">downgraded</Badge>}
                      </td>
                    </tr>
                  ))}
                </Table>
              ))}

            {tab === "approvals" &&
              (data.approvals.length === 0 ? (
                <Empty title="No approvals for this task" />
              ) : (
                <ul className="space-y-2">
                  {data.approvals.map((a) => (
                    <li key={a.id} className="flex items-center gap-3 rounded-lg px-3 py-2 ring-1 ring-slate-200">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{a.title}</div>
                        <div className="text-xs text-slate-500">{a.comment || a.reason}</div>
                      </div>
                      <RiskBadge level={a.risk_level} />
                      <StatusBadge status={a.status} />
                      <Link to={`/approvals/${a.id}`} className="text-slate-400 hover:text-brand-600"><ExternalLink className="h-4 w-4" /></Link>
                    </li>
                  ))}
                </ul>
              ))}
          </div>
        </div>
      )}
    </Drawer>
  );
}
