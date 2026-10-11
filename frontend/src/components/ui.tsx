import clsx from "clsx";
import { AlertTriangle, Inbox, Loader2, X } from "lucide-react";
import { useEffect, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

import { titleCase } from "../lib/format";

export const STATUS_STYLES: Record<string, { badge: string; dot: string; node: string }> = {
  PENDING: { badge: "bg-slate-100 text-slate-600 ring-slate-200", dot: "bg-slate-400", node: "border-slate-300 bg-white" },
  READY: { badge: "bg-sky-50 text-sky-700 ring-sky-200", dot: "bg-sky-400", node: "border-sky-300 bg-sky-50" },
  QUEUED: { badge: "bg-sky-50 text-sky-700 ring-sky-200", dot: "bg-sky-500", node: "border-sky-300 bg-sky-50" },
  PLANNING: { badge: "bg-violet-50 text-violet-700 ring-violet-200", dot: "bg-violet-500", node: "border-violet-300 bg-violet-50" },
  RUNNING: { badge: "bg-blue-50 text-blue-700 ring-blue-200", dot: "bg-blue-500", node: "border-blue-400 bg-blue-50 node-running" },
  WAITING_APPROVAL: { badge: "bg-amber-50 text-amber-700 ring-amber-200", dot: "bg-amber-500", node: "border-amber-400 bg-amber-50" },
  WAITING_CHILDREN: { badge: "bg-indigo-50 text-indigo-700 ring-indigo-200", dot: "bg-indigo-400", node: "border-indigo-300 bg-indigo-50" },
  IN_REVIEW: { badge: "bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200", dot: "bg-fuchsia-500", node: "border-fuchsia-300 bg-fuchsia-50" },
  REVISION_REQUESTED: { badge: "bg-orange-50 text-orange-700 ring-orange-200", dot: "bg-orange-500", node: "border-orange-400 bg-orange-50" },
  RETRY_SCHEDULED: { badge: "bg-orange-50 text-orange-700 ring-orange-200", dot: "bg-orange-400", node: "border-orange-300 bg-orange-50" },
  ESCALATED: { badge: "bg-rose-50 text-rose-700 ring-rose-200", dot: "bg-rose-500", node: "border-rose-400 bg-rose-50" },
  COMPLETED: { badge: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500", node: "border-emerald-300 bg-white" },
  FAILED: { badge: "bg-red-50 text-red-700 ring-red-200", dot: "bg-red-500", node: "border-red-400 bg-red-50" },
  CANCELLED: { badge: "bg-slate-100 text-slate-500 ring-slate-200", dot: "bg-slate-400", node: "border-slate-300 bg-slate-50 opacity-70" },
  SKIPPED: { badge: "bg-slate-100 text-slate-500 ring-slate-200", dot: "bg-slate-300", node: "border-slate-200 bg-slate-50 opacity-60" },
  APPROVED: { badge: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500", node: "" },
  REJECTED: { badge: "bg-red-50 text-red-700 ring-red-200", dot: "bg-red-500", node: "" },
  EDITED: { badge: "bg-teal-50 text-teal-700 ring-teal-200", dot: "bg-teal-500", node: "" },
  CHANGES_REQUESTED: { badge: "bg-orange-50 text-orange-700 ring-orange-200", dot: "bg-orange-500", node: "" },
  EXPIRED: { badge: "bg-slate-100 text-slate-500 ring-slate-200", dot: "bg-slate-400", node: "" },
  SUCCESS: { badge: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500", node: "" },
  ERROR: { badge: "bg-red-50 text-red-700 ring-red-200", dot: "bg-red-500", node: "" },
  DENIED: { badge: "bg-red-50 text-red-700 ring-red-200", dot: "bg-red-500", node: "" },
  TIMEOUT: { badge: "bg-orange-50 text-orange-700 ring-orange-200", dot: "bg-orange-500", node: "" },
  APPROVAL_REQUIRED: { badge: "bg-amber-50 text-amber-700 ring-amber-200", dot: "bg-amber-500", node: "" },
  INDEXED: { badge: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500", node: "" },
  VERIFIED: { badge: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500", node: "" },
  UNVERIFIED: { badge: "bg-amber-50 text-amber-700 ring-amber-200", dot: "bg-amber-500", node: "" },
  HUMAN_APPROVED: { badge: "bg-teal-50 text-teal-700 ring-teal-200", dot: "bg-teal-500", node: "" },
  OPEN: { badge: "bg-sky-50 text-sky-700 ring-sky-200", dot: "bg-sky-500", node: "" },
  RESOLVED: { badge: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500", node: "" },
};

const FALLBACK = { badge: "bg-slate-100 text-slate-600 ring-slate-200", dot: "bg-slate-400", node: "border-slate-300 bg-white" };
export const statusStyle = (s: string) => STATUS_STYLES[(s ?? "").toUpperCase()] ?? FALLBACK;

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const st = statusStyle(status);
  return (
    <span className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", st.badge, className)}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", st.dot, status === "RUNNING" && "animate-pulse")} />
      {titleCase(status)}
    </span>
  );
}

const RISK: Record<string, string> = {
  low: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  medium: "bg-amber-50 text-amber-700 ring-amber-200",
  high: "bg-rose-50 text-rose-700 ring-rose-200",
  critical: "bg-red-100 text-red-800 ring-red-300",
};

export function RiskBadge({ level }: { level: string }) {
  return <Badge className={RISK[level] ?? RISK.medium}>{titleCase(level || "unknown")} risk</Badge>;
}

export function Badge({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", className ?? "bg-slate-50 text-slate-600 ring-slate-200")}>
      {children}
    </span>
  );
}

export function Card({ title, actions, children, className, bodyClass, subtitle }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
}) {
  return (
    <section className={clsx("rounded-xl border border-slate-200 bg-white shadow-sm", className)}>
      {(title || actions || subtitle) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div className="min-w-0">
            {title && <h3 className="truncate text-sm font-semibold text-slate-900">{title}</h3>}
            {subtitle && <p className="truncate text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx(bodyClass ?? "p-4")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, icon, tone = "slate" }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: "slate" | "brand" | "emerald" | "amber" | "rose" | "sky";
}) {
  const tones = {
    slate: "bg-slate-100 text-slate-600",
    brand: "bg-brand-50 text-brand-600",
    emerald: "bg-emerald-50 text-emerald-600",
    amber: "bg-amber-50 text-amber-600",
    rose: "bg-rose-50 text-rose-600",
    sky: "bg-sky-50 text-sky-600",
  };
  return (
    <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      {icon && <div className={clsx("rounded-lg p-2", tones[tone])}>{icon}</div>}
      <div className="min-w-0">
        <div className="text-xs font-medium text-slate-500">{label}</div>
        <div className="mt-0.5 text-xl font-semibold tracking-tight text-slate-900">{value}</div>
        {hint && <div className="mt-0.5 truncate text-xs text-slate-500">{hint}</div>}
      </div>
    </div>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700 shadow-sm disabled:bg-brand-300",
  secondary: "bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 shadow-sm disabled:text-slate-400",
  ghost: "text-slate-600 hover:bg-slate-100 disabled:text-slate-300",
  danger: "bg-rose-600 text-white hover:bg-rose-700 shadow-sm disabled:bg-rose-300",
  success: "bg-emerald-600 text-white hover:bg-emerald-700 shadow-sm disabled:bg-emerald-300",
};

export function Button({ variant = "secondary", size = "md", loading, icon, children, className, disabled, type = "button", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean; icon?: ReactNode }) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm",
        VARIANTS[variant],
        className,
      )}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function Field({ label, hint, error, children, className }: { label: ReactNode; hint?: ReactNode; error?: string | null; children: ReactNode; className?: string }) {
  return (
    <label className={clsx("block", className)}>
      <span className="mb-1 block text-xs font-medium text-slate-700">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-rose-600">{error}</span>}
    </label>
  );
}

const INPUT = "block w-full rounded-lg border-0 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-brand-500 disabled:bg-slate-50 disabled:text-slate-500";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(INPUT, props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(INPUT, props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(INPUT, "pr-8", props.className)} />;
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={clsx("relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50", checked ? "bg-brand-600" : "bg-slate-300")}
    >
      <span className={clsx("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform", checked ? "translate-x-4" : "translate-x-0.5")} />
    </button>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label ?? "Loading…"}
    </div>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
      <div className="rounded-full bg-slate-100 p-3 text-slate-400">{icon ?? <Inbox className="h-5 w-5" />}</div>
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {children && <div className="max-w-md text-xs text-slate-500">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="break-words">{msg}</span>
    </div>
  );
}

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

export function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEscape(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[8vh] backdrop-blur-[2px]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className={clsx("w-full rounded-xl bg-white shadow-2xl ring-1 ring-slate-200", wide ? "max-w-3xl" : "max-w-lg")}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
          <h2 className="text-base font-semibold text-slate-900">{title}</h2>
          <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 rounded-b-xl border-t border-slate-100 bg-slate-50/60 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, subtitle, children, actions }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  useEscape(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/20" onMouseDown={onClose}>
      <aside role="dialog" className="flex h-full w-full max-w-3xl flex-col bg-white shadow-2xl ring-1 ring-slate-200" onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-slate-900">{title}</h2>
            {subtitle && <div className="mt-1 text-xs text-slate-500">{subtitle}</div>}
          </div>
          <div className="flex items-center gap-2">
            {actions}
            <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600" aria-label="Close">
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>
        <div className="scrollbar-thin flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, className }: {
  tabs: { key: T; label: ReactNode; count?: number }[];
  value: T;
  onChange: (k: T) => void;
  className?: string;
}) {
  return (
    <div className={clsx("flex gap-1 overflow-x-auto border-b border-slate-200", className)} role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={value === t.key}
          onClick={() => onChange(t.key)}
          className={clsx(
            "-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            value === t.key ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800",
          )}
        >
          {t.label}
          {t.count !== undefined && (
            <span className={clsx("rounded-full px-1.5 text-[11px]", value === t.key ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-500")}>{t.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export function JsonView({ value, className, collapsed = false }: { value: unknown; className?: string; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  const text = JSON.stringify(value, null, 2) ?? "null";
  if (!open) {
    return (
      <button className="text-xs font-medium text-brand-600 hover:underline" onClick={() => setOpen(true)}>
        Show JSON ({text.length.toLocaleString()} chars)
      </button>
    );
  }
  return (
    <pre className={clsx("scrollbar-thin max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100", className)}>
      {text}
    </pre>
  );
}

export function ProgressBar({ value, tone = "brand", className }: { value: number; tone?: "brand" | "emerald" | "amber" | "rose"; className?: string }) {
  const colors = { brand: "bg-brand-500", emerald: "bg-emerald-500", amber: "bg-amber-500", rose: "bg-rose-500" };
  return (
    <div className={clsx("h-1.5 w-full overflow-hidden rounded-full bg-slate-100", className)}>
      <div className={clsx("h-full rounded-full transition-all", colors[tone])} style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} />
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={clsx("scrollbar-thin overflow-x-auto", className)}>
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export function KV({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={clsx("grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm", className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd className="min-w-0 break-words text-slate-900">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function TagInput({ value, onChange, options, placeholder }: { value: string[]; onChange: (v: string[]) => void; options: string[]; placeholder?: string }) {
  const remaining = options.filter((o) => !value.includes(o));
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg bg-white p-1.5 ring-1 ring-inset ring-slate-300">
      {value.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 font-mono text-[11px] text-brand-700">
          {v}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(value.filter((x) => x !== v))} className="text-brand-400 hover:text-brand-700">
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      {remaining.length > 0 && (
        <select
          value=""
          onChange={(e) => e.target.value && onChange([...value, e.target.value])}
          className="min-w-32 flex-1 border-0 bg-transparent py-0.5 text-xs text-slate-500 focus:ring-0"
        >
          <option value="">{placeholder ?? "Add…"}</option>
          {remaining.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
      )}
    </div>
  );
}
