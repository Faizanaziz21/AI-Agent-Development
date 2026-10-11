import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { Download, Link2, ShieldCheck, ShieldX } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge, Button, Card, Drawer, Empty, ErrorBox, Input, JsonView, KV, PageHeader, Select, Spinner, Table } from "../components/ui";
import { api, qs } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateTime, timeAgo, truncate } from "../lib/format";
import type { AuditRecord, User } from "../lib/types";

const PAGE = 100;
const RESOURCES = ["project", "approval", "tool_call", "tool", "policy", "agent", "knowledge_source", "memory", "organization", "user", "secret", "webhook"];
type Verify = { valid: boolean; records: number; head?: string; broken_at_seq?: number };

export default function Audit() {
  const { can } = useAuth();
  const [action, setAction] = useState("");
  const [resource, setResource] = useState("");
  const [actor, setActor] = useState("");
  const [selected, setSelected] = useState<AuditRecord | null>(null);
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users"), enabled: can("audit.read") });
  const names = new Map((users.data ?? []).map((u) => [u.id, u.name]));
  const log = useInfiniteQuery({
    queryKey: ["audit", action, resource, actor],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => api.get<AuditRecord[]>(`/audit${qs({ action, resource_type: resource, actor_id: actor, before_seq: pageParam, limit: PAGE })}`),
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].seq : undefined),
    enabled: can("audit.read"),
  });
  const verify = useMutation({ mutationFn: () => api.get<Verify>("/audit/verify") });
  const rows = log.data?.pages.flat() ?? [];

  if (!can("audit.read")) {
    return (
      <div>
        <PageHeader title="Audit log" />
        <Empty title="Audit access required">The audit log is available to compliance officers, admins and owners.</Empty>
      </div>
    );
  }

  const exportJson = () => {
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `agentos-audit-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Append-only and hash-chained: every record stores the SHA-256 of its predecessor, so any edit or deletion breaks the chain. Covers human decisions, agent tool calls, policy changes, configuration and data access."
        actions={
          <>
            <Button icon={<Download className="h-4 w-4" />} onClick={exportJson} disabled={rows.length === 0}>Export loaded</Button>
            <Button variant="primary" icon={<Link2 className="h-4 w-4" />} loading={verify.isPending} onClick={() => verify.mutate()}>Verify chain</Button>
          </>
        }
      />
      {verify.data && (
        <div className={`mb-5 flex items-center gap-3 rounded-xl border px-4 py-3 text-sm ${verify.data.valid ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}>
          {verify.data.valid ? <ShieldCheck className="h-5 w-5" /> : <ShieldX className="h-5 w-5" />}
          {verify.data.valid ? (
            <span>Chain intact across {verify.data.records.toLocaleString()} records. Head <code className="font-mono text-xs">{truncate(verify.data.head ?? "", 20)}</code></span>
          ) : (
            <span>Chain broken at record #{verify.data.broken_at_seq}. Records from that point may have been altered.</span>
          )}
        </div>
      )}
      <ErrorBox error={verify.error} />
      <Card bodyClass="">
        <div className="flex flex-wrap gap-2 border-b border-slate-100 p-3">
          <Input aria-label="Action prefix" placeholder="Action prefix, e.g. approval. or tool.call" value={action} onChange={(e) => setAction(e.target.value.trim())} className="w-72" />
          <Select aria-label="Resource type" value={resource} onChange={(e) => setResource(e.target.value)} className="w-48">
            <option value="">All resources</option>
            {RESOURCES.map((r) => <option key={r} value={r}>{r}</option>)}
          </Select>
          <Select aria-label="Actor" value={actor} onChange={(e) => setActor(e.target.value)} className="w-48">
            <option value="">All actors</option>
            {(users.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </Select>
        </div>
        {log.isLoading ? <Spinner /> : log.error ? <div className="p-3"><ErrorBox error={log.error} /></div> : rows.length === 0 ? <Empty title="No audit records match" /> : (
          <>
            <Table head={["#", "When", "Actor", "Action", "Resource", "Details", "Hash"]}>
              {rows.map((r) => (
                <tr key={r.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelected(r)}>
                  <td className="px-3 py-2 font-mono text-[11px] text-slate-400">{r.seq}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-500" title={dateTime(r.created_at)}>{timeAgo(r.created_at)}</td>
                  <td className="px-3 py-2 text-xs">
                    <Badge className={r.actor_type === "user" ? "bg-sky-50 text-sky-700 ring-sky-200" : r.actor_type === "agent" ? "bg-violet-50 text-violet-700 ring-violet-200" : ""}>{r.actor_type}</Badge>{" "}
                    {names.get(r.actor_id) ?? r.actor_id}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-800">{r.action}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">{r.resource_type}</td>
                  <td className="max-w-md truncate px-3 py-2 font-mono text-[11px] text-slate-500">{truncate(JSON.stringify(r.details), 120)}</td>
                  <td className="px-3 py-2 font-mono text-[11px] text-slate-400">{r.hash.slice(0, 10)}</td>
                </tr>
              ))}
            </Table>
            {log.hasNextPage && (
              <div className="flex justify-center p-3">
                <Button loading={log.isFetchingNextPage} onClick={() => log.fetchNextPage()}>Load older records</Button>
              </div>
            )}
          </>
        )}
      </Card>
      <Drawer open={!!selected} onClose={() => setSelected(null)} title={selected ? `#${selected.seq} ${selected.action}` : ""} subtitle={selected && dateTime(selected.created_at)}>
        {selected && (
          <div className="space-y-4 p-5">
            <KV items={[
              ["Actor", `${selected.actor_type} · ${names.get(selected.actor_id) ?? selected.actor_id}`],
              ["Resource", `${selected.resource_type} · ${selected.resource_id}`],
              ["Project", selected.project_id ? <Link key="p" className="text-brand-600 hover:underline" to={`/projects/${selected.project_id}`}>{selected.project_id}</Link> : "—"],
              ["IP", selected.ip ?? "—"],
              ["Previous hash", <code key="ph" className="break-all font-mono text-[11px]">{selected.prev_hash}</code>],
              ["Hash", <code key="h" className="break-all font-mono text-[11px]">{selected.hash}</code>],
            ]} />
            <JsonView value={selected.details} />
          </div>
        )}
      </Drawer>
    </div>
  );
}
