import type { ReactNode } from "react";

import { titleCase } from "../lib/format";
import type { Json } from "../lib/types";
import { Badge, JsonView, Table } from "./ui";

const HIDDEN = new Set(["task_key"]);
const PROSE_KEYS = ["headline", "summary", "executive_summary", "recommendation", "reply"];

function isRecordArray(v: unknown): v is Record<string, unknown>[] {
  return Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === "object" && !Array.isArray(x));
}

function cell(v: unknown): ReactNode {
  if (v === null || v === undefined || v === "") return <span className="text-slate-300">—</span>;
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(v < 1 ? 3 : 2);
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "string") return v.length > 160 ? `${v.slice(0, 159)}…` : v;
  if (Array.isArray(v) && v.every((x) => typeof x !== "object")) return v.join(", ");
  return <span className="font-mono text-[11px] text-slate-500">{JSON.stringify(v).slice(0, 120)}</span>;
}

export function RecordTable({ rows, max = 50 }: { rows: Record<string, unknown>[]; max?: number }) {
  const cols = [...new Set(rows.slice(0, 20).flatMap((r) => Object.keys(r)))]
    .filter((c) => rows.some((r) => typeof r[c] !== "object" || r[c] === null || (Array.isArray(r[c]) && (r[c] as unknown[]).every((x) => typeof x !== "object"))))
    .slice(0, 9);
  return (
    <div className="rounded-lg ring-1 ring-slate-200">
      <Table head={cols.map(titleCase)}>
        {rows.slice(0, max).map((r, i) => (
          <tr key={i} className="align-top">
            {cols.map((c) => (
              <td key={c} className="max-w-xs px-3 py-1.5 text-xs text-slate-700">{cell(r[c])}</td>
            ))}
          </tr>
        ))}
      </Table>
      {rows.length > max && <div className="border-t border-slate-100 px-3 py-1.5 text-xs text-slate-500">Showing {max} of {rows.length}</div>}
    </div>
  );
}

function Section({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  const label = <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">{titleCase(name)}</h4>;
  if (typeof value === "string") {
    return (
      <div>
        {label}
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">{value}</p>
      </div>
    );
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return (
      <div className="flex items-baseline justify-between gap-4 border-b border-slate-100 py-1 text-sm">
        <span className="text-slate-500">{titleCase(name)}</span>
        <span className="font-medium text-slate-900">{cell(value)}</span>
      </div>
    );
  }
  if (isRecordArray(value)) {
    return (
      <div>
        {label}
        <RecordTable rows={value} />
      </div>
    );
  }
  if (Array.isArray(value)) {
    return (
      <div>
        {label}
        <div className="flex flex-wrap gap-1.5">
          {value.map((v, i) => (
            <Badge key={i}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</Badge>
          ))}
        </div>
      </div>
    );
  }
  if (value && typeof value === "object" && depth < 2) {
    return (
      <div className="rounded-lg bg-slate-50/70 p-3 ring-1 ring-slate-100">
        {label}
        <DeliverableBody data={value as Json} depth={depth + 1} />
      </div>
    );
  }
  return (
    <div>
      {label}
      <JsonView value={value} collapsed />
    </div>
  );
}

function DeliverableBody({ data, depth }: { data: Json; depth: number }) {
  const keys = Object.keys(data).filter((k) => !HIDDEN.has(k));
  const prose = keys.filter((k) => PROSE_KEYS.includes(k) && typeof data[k] === "string");
  const scalars = keys.filter((k) => !prose.includes(k) && ["number", "boolean"].includes(typeof data[k]));
  const rest = keys.filter((k) => !prose.includes(k) && !scalars.includes(k));
  return (
    <div className="space-y-5">
      {prose.map((k) =>
        k === "headline" ? (
          <p key={k} className="text-lg font-semibold leading-snug text-slate-900">{String(data[k])}</p>
        ) : (
          <Section key={k} name={k} value={data[k]} depth={depth} />
        ),
      )}
      {scalars.length > 0 && (
        <div className="grid gap-x-8 sm:grid-cols-2 lg:grid-cols-3">
          {scalars.map((k) => <Section key={k} name={k} value={data[k]} depth={depth} />)}
        </div>
      )}
      {rest.map((k) => <Section key={k} name={k} value={data[k]} depth={depth} />)}
    </div>
  );
}

export function Deliverable({ data }: { data: Json }) {
  return <DeliverableBody data={data} depth={0} />;
}
