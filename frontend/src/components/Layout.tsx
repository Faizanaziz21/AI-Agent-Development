import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  Bell,
  BookOpen,
  Bot,
  Brain,
  CheckSquare,
  CircleDollarSign,
  FolderKanban,
  Headset,
  LayoutDashboard,
  LogOut,
  ScrollText,
  Settings,
  Wrench,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { timeAgo, titleCase } from "../lib/format";
import type { Notification } from "../lib/types";

const NAV: { to: string; label: string; icon: ReactNode; perm?: string; group: string }[] = [
  { to: "/", label: "Dashboard", icon: <LayoutDashboard className="h-4 w-4" />, group: "Operate" },
  { to: "/projects", label: "Projects", icon: <FolderKanban className="h-4 w-4" />, group: "Operate" },
  { to: "/support", label: "Support Desk", icon: <Headset className="h-4 w-4" />, group: "Operate" },
  { to: "/approvals", label: "Approvals", icon: <CheckSquare className="h-4 w-4" />, group: "Operate" },
  { to: "/agents", label: "Agent Library", icon: <Bot className="h-4 w-4" />, group: "Build" },
  { to: "/tools", label: "Tool Registry", icon: <Wrench className="h-4 w-4" />, group: "Build" },
  { to: "/knowledge", label: "Knowledge Base", icon: <BookOpen className="h-4 w-4" />, group: "Build" },
  { to: "/memory", label: "Memory", icon: <Brain className="h-4 w-4" />, group: "Build" },
  { to: "/costs", label: "Costs & Models", icon: <CircleDollarSign className="h-4 w-4" />, group: "Govern" },
  { to: "/audit", label: "Audit Log", icon: <ScrollText className="h-4 w-4" />, perm: "audit.read", group: "Govern" },
  { to: "/settings", label: "Settings", icon: <Settings className="h-4 w-4" />, group: "Govern" },
];

function Notifications() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data = [] } = useQuery({ queryKey: ["notifications"], queryFn: () => api.get<Notification[]>("/notifications"), refetchInterval: 10_000 });
  const unread = data.filter((n) => !n.read).length;
  const readAll = useMutation({ mutationFn: () => api.post("/notifications/read-all"), onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }) });
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="relative rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-700" aria-label="Notifications">
        <Bell className="h-5 w-5" />
        {unread > 0 && (
          <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-40 mt-2 w-96 overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
              <span className="text-sm font-semibold">Notifications</span>
              {unread > 0 && (
                <button className="text-xs font-medium text-brand-600 hover:underline" onClick={() => readAll.mutate()}>
                  Mark all read
                </button>
              )}
            </div>
            <div className="scrollbar-thin max-h-96 overflow-y-auto">
              {data.length === 0 && <div className="px-4 py-8 text-center text-sm text-slate-500">You're all caught up.</div>}
              {data.slice(0, 30).map((n) => (
                <button
                  key={n.id}
                  onClick={() => {
                    setOpen(false);
                    if (n.link) navigate(n.link);
                  }}
                  className={clsx("block w-full border-b border-slate-50 px-4 py-2.5 text-left hover:bg-slate-50", !n.read && "bg-brand-50/40")}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-slate-800">{n.title}</span>
                    <span className="shrink-0 text-[11px] text-slate-400">{timeAgo(n.created_at)}</span>
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-xs text-slate-500">{n.body}</div>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function Layout() {
  const { me, logout, can } = useAuth();
  const { data: summary } = useQuery({
    queryKey: ["approvals", "summary"],
    queryFn: () => api.get<{ by_status: Record<string, number>; pending_by_risk: Record<string, number> }>("/approvals/summary"),
    refetchInterval: 5_000,
  });
  const { data: status } = useQuery({
    queryKey: ["system", "status"],
    queryFn: () => api.get<{ active_tasks: number; queue: { depth: number } }>("/system/status"),
    refetchInterval: 5_000,
  });
  const pending = summary?.by_status?.PENDING ?? 0;
  const groups = [...new Set(NAV.map((n) => n.group))];
  const initials = (me?.user.name ?? "")
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2);

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <Link to="/" className="flex items-center gap-2.5 px-5 py-5">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <div>
            <div className="text-[15px] font-semibold leading-tight text-slate-900">AgentOS</div>
            <div className="text-[11px] leading-tight text-slate-500">Digital Workforce Platform</div>
          </div>
        </Link>
        <nav className="scrollbar-thin flex-1 space-y-5 overflow-y-auto px-3 pb-4">
          {groups.map((g) => (
            <div key={g}>
              <div className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{g}</div>
              <div className="space-y-0.5">
                {NAV.filter((n) => n.group === g && (!n.perm || can(n.perm))).map((n) => (
                  <NavLink
                    key={n.to}
                    to={n.to}
                    end={n.to === "/"}
                    className={({ isActive }) =>
                      clsx(
                        "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors",
                        isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                      )
                    }
                  >
                    {n.icon}
                    <span className="flex-1">{n.label}</span>
                    {n.to === "/approvals" && pending > 0 && (
                      <span className="rounded-full bg-amber-100 px-1.5 text-[11px] font-semibold text-amber-700">{pending}</span>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="border-t border-slate-100 px-4 py-3">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Activity className={clsx("h-3.5 w-3.5", (status?.active_tasks ?? 0) > 0 ? "text-emerald-500" : "text-slate-400")} />
            {status ? `${status.active_tasks} active tasks · queue ${status.queue.depth}` : "Connecting…"}
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-6">
          <div className="min-w-0 truncate text-sm text-slate-500">
            <span className="font-medium text-slate-800">{me?.organization.name}</span>
            <span className="mx-2 text-slate-300">/</span>
            <span className="capitalize">{me?.organization.plan} plan</span>
          </div>
          <div className="flex items-center gap-2">
            <Notifications />
            <div className="hidden text-right sm:block">
              <div className="text-sm font-medium leading-tight text-slate-800">{me?.user.name}</div>
              <div className="text-[11px] leading-tight text-slate-500">{titleCase(me?.user.role ?? "")}</div>
            </div>
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700">{initials}</div>
            <button onClick={logout} className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700" title="Sign out" aria-label="Sign out">
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </header>
        <main className="scrollbar-thin flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1440px] px-6 py-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
