export function usd(v: number | null | undefined, digits?: number): string {
  const n = v ?? 0;
  const d = digits ?? (Math.abs(n) < 1 ? 4 : 2);
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

export function compact(v: number | null | undefined): string {
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v ?? 0);
}

export function pct(v: number | null | undefined, digits = 0): string {
  return `${((v ?? 0) * 100).toFixed(digits)}%`;
}

/** Backend timestamps are naive UTC ISO strings. */
export function parseTs(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(ts) ? ts : `${ts}Z`);
}

export function timeAgo(ts: string | null | undefined, now: number = Date.now()): string {
  const d = parseTs(ts);
  if (!d) return "—";
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString();
}

export function clock(ts: string | null | undefined): string {
  const d = parseTs(ts);
  return d ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}

export function dateTime(ts: string | null | undefined): string {
  const d = parseTs(ts);
  return d ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" }) : "—";
}

export function duration(start: string | null | undefined, end: string | null | undefined, now: number = Date.now()): string {
  const a = parseTs(start);
  if (!a) return "—";
  const b = parseTs(end)?.getTime() ?? now;
  const s = Math.max(0, (b - a.getTime()) / 1000);
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function titleCase(s: string): string {
  return (s ?? "").replace(/[_.]/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export function truncate(s: string | null | undefined, n: number): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
