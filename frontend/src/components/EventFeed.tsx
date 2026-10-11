import clsx from "clsx";
import {
  AlertTriangle,
  Ban,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  ClipboardCheck,
  Cpu,
  GitBranch,
  Hammer,
  LifeBuoy,
  ListTree,
  PlayCircle,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { clock, titleCase } from "../lib/format";
import type { EventItem } from "../lib/types";

type Tone = "slate" | "blue" | "emerald" | "amber" | "rose" | "violet" | "fuchsia" | "orange";

const TONES: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-500",
  blue: "bg-blue-50 text-blue-600",
  emerald: "bg-emerald-50 text-emerald-600",
  amber: "bg-amber-50 text-amber-600",
  rose: "bg-rose-50 text-rose-600",
  violet: "bg-violet-50 text-violet-600",
  fuchsia: "bg-fuchsia-50 text-fuchsia-600",
  orange: "bg-orange-50 text-orange-600",
};

const I = "h-3.5 w-3.5";
export const EVENT_META: Record<string, { icon: ReactNode; tone: Tone; group: string }> = {
  PROJECT_CREATED: { icon: <Sparkles className={I} />, tone: "violet", group: "project" },
  PROJECT_STARTED: { icon: <PlayCircle className={I} />, tone: "violet", group: "project" },
  PLAN_CREATED: { icon: <ListTree className={I} />, tone: "violet", group: "project" },
  PROJECT_COMPLETED: { icon: <CheckCircle2 className={I} />, tone: "emerald", group: "project" },
  PROJECT_FAILED: { icon: <XCircle className={I} />, tone: "rose", group: "project" },
  PROJECT_CANCELLED: { icon: <Ban className={I} />, tone: "slate", group: "project" },
  TASK_CREATED: { icon: <ListTree className={I} />, tone: "slate", group: "task" },
  TASK_ASSIGNED: { icon: <Bot className={I} />, tone: "slate", group: "task" },
  TASK_QUEUED: { icon: <ListTree className={I} />, tone: "slate", group: "task" },
  TASK_DELEGATED: { icon: <GitBranch className={I} />, tone: "violet", group: "task" },
  TASK_COMPLETED: { icon: <CheckCircle2 className={I} />, tone: "emerald", group: "task" },
  TASK_FAILED: { icon: <XCircle className={I} />, tone: "rose", group: "task" },
  TASK_RETRY_SCHEDULED: { icon: <RotateCcw className={I} />, tone: "orange", group: "task" },
  TASK_BLOCKED: { icon: <Ban className={I} />, tone: "orange", group: "task" },
  TASK_CANCELLED: { icon: <Ban className={I} />, tone: "slate", group: "task" },
  TASK_ESCALATED: { icon: <LifeBuoy className={I} />, tone: "rose", group: "task" },
  TASK_REASSIGNED: { icon: <Bot className={I} />, tone: "orange", group: "task" },
  TASK_RECOVERED: { icon: <RotateCcw className={I} />, tone: "orange", group: "task" },
  TASK_REVIEW_STARTED: { icon: <ClipboardCheck className={I} />, tone: "fuchsia", group: "qa" },
  TASK_REVISION_REQUESTED: { icon: <RotateCcw className={I} />, tone: "fuchsia", group: "qa" },
  AGENT_STARTED: { icon: <Bot className={I} />, tone: "blue", group: "agent" },
  AGENT_STEP: { icon: <Cpu className={I} />, tone: "blue", group: "agent" },
  TOOL_CALLED: { icon: <Hammer className={I} />, tone: "slate", group: "tool" },
  TOOL_COMPLETED: { icon: <Hammer className={I} />, tone: "emerald", group: "tool" },
  TOOL_FAILED: { icon: <Hammer className={I} />, tone: "rose", group: "tool" },
  TOOL_DENIED: { icon: <ShieldAlert className={I} />, tone: "rose", group: "tool" },
  MODEL_FALLBACK: { icon: <Cpu className={I} />, tone: "orange", group: "model" },
  MODEL_DOWNGRADED: { icon: <CircleDollarSign className={I} />, tone: "orange", group: "model" },
  APPROVAL_REQUIRED: { icon: <UserCheck className={I} />, tone: "amber", group: "approval" },
  APPROVAL_GRANTED: { icon: <ShieldCheck className={I} />, tone: "emerald", group: "approval" },
  APPROVAL_REJECTED: { icon: <XCircle className={I} />, tone: "rose", group: "approval" },
  APPROVAL_CHANGES_REQUESTED: { icon: <RotateCcw className={I} />, tone: "amber", group: "approval" },
  BUDGET_WARNING: { icon: <CircleDollarSign className={I} />, tone: "amber", group: "budget" },
  BUDGET_EXCEEDED: { icon: <CircleDollarSign className={I} />, tone: "rose", group: "budget" },
  INJECTION_DETECTED: { icon: <AlertTriangle className={I} />, tone: "rose", group: "security" },
  MEMORY_WRITTEN: { icon: <Sparkles className={I} />, tone: "violet", group: "memory" },
};

export const eventMeta = (type: string) => EVENT_META[type] ?? { icon: <Sparkles className={I} />, tone: "slate" as Tone, group: "other" };

const FILTERS = [
  { key: "all", label: "All" },
  { key: "important", label: "Key events" },
  { key: "agent", label: "Agent steps" },
  { key: "tool", label: "Tools" },
  { key: "approval", label: "Approvals" },
  { key: "security", label: "Security" },
] as const;

const IMPORTANT = new Set(["project", "qa", "approval", "budget", "security", "model", "memory"]);
const IMPORTANT_TYPES = new Set(["TASK_COMPLETED", "TASK_FAILED", "TASK_DELEGATED", "TASK_ESCALATED", "TASK_RECOVERED", "TASK_RETRY_SCHEDULED", "TOOL_DENIED", "TOOL_FAILED"]);

export function EventRow({ e, onSelectTask, agentLabel }: { e: EventItem; onSelectTask?: (id: string) => void; agentLabel?: string }) {
  const m = eventMeta(e.type);
  return (
    <li className="flex gap-3 px-4 py-2 hover:bg-slate-50/80">
      <span className={clsx("mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md", TONES[m.tone])}>{m.icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <span className="font-mono">{clock(e.created_at)}</span>
          <span className="font-semibold uppercase tracking-wide">{titleCase(e.type)}</span>
          {(agentLabel || e.agent_key) && <span className="truncate">· {agentLabel ?? e.agent_key}</span>}
        </div>
        <div className="text-[13px] leading-snug text-slate-700">
          {e.task_id && onSelectTask ? (
            <button className="text-left hover:text-brand-700 hover:underline" onClick={() => onSelectTask(e.task_id!)}>
              {e.message}
            </button>
          ) : (
            e.message
          )}
        </div>
      </div>
    </li>
  );
}

export function EventFeed({ events, onSelectTask, height = 520, follow = true, state }: {
  events: EventItem[];
  onSelectTask?: (id: string) => void;
  height?: number | string;
  follow?: boolean;
  state?: string;
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("important");
  const [autoScroll, setAutoScroll] = useState(follow);
  const ref = useRef<HTMLUListElement>(null);
  const shown = useMemo(
    () =>
      events.filter((e) => {
        const g = eventMeta(e.type).group;
        if (filter === "all") return true;
        if (filter === "important") return IMPORTANT.has(g) || IMPORTANT_TYPES.has(e.type);
        return g === filter;
      }),
    [events, filter],
  );
  useEffect(() => {
    if (autoScroll && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [shown.length, autoScroll]);

  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white" style={{ height }}>
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
        <div className="flex gap-1 overflow-x-auto">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={clsx("whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium", filter === f.key ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:bg-slate-100")}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-3 text-[11px] text-slate-500">
          {state && (
            <span className="flex items-center gap-1.5">
              <span className={clsx("h-2 w-2 rounded-full", state === "live" ? "animate-pulse bg-emerald-500" : state === "closed" ? "bg-slate-300" : "bg-amber-400")} />
              {state === "live" ? "Live" : titleCase(state)}
            </span>
          )}
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} className="h-3 w-3 accent-brand-600" />
            Follow
          </label>
        </div>
      </div>
      <ul ref={ref} className="scrollbar-thin flex-1 divide-y divide-slate-50 overflow-y-auto py-1">
        {shown.length === 0 && <li className="px-4 py-8 text-center text-sm text-slate-400">No events yet.</li>}
        {shown.map((e) => (
          <EventRow key={e.id} e={e} onSelectTask={onSelectTask} />
        ))}
      </ul>
    </div>
  );
}
