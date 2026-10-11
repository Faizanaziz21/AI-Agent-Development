import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, MessageSquareWarning, Pencil, ShieldAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { RecordTable } from "../components/Deliverable";
import { Badge, Button, Card, Empty, ErrorBox, JsonView, KV, PageHeader, RiskBadge, Spinner, StatusBadge, Tabs, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, timeAgo, titleCase } from "../lib/format";
import type { Approval } from "../lib/types";

type Decision = "approve" | "reject" | "edit" | "request_changes";

function PayloadPreview({ payload }: { payload: Record<string, unknown> }) {
  const messages = payload.messages as Record<string, unknown>[] | undefined;
  const records = Object.entries(payload).find(([, v]) => Array.isArray(v) && v.length && typeof v[0] === "object");
  if (Array.isArray(messages) && messages.length && typeof messages[0] === "object") {
    return (
      <div className="space-y-2">
        <div className="text-xs text-slate-500">{messages.length} messages will be sent</div>
        <div className="scrollbar-thin max-h-96 space-y-2 overflow-y-auto pr-1">
          {messages.slice(0, 50).map((m, i) => (
            <details key={i} className="rounded-lg ring-1 ring-slate-200">
              <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm">
                <span className="font-medium">{String(m.to ?? m.recipient ?? "")}</span>
                <span className="truncate text-slate-500">— {String(m.subject ?? "")}</span>
              </summary>
              <pre className="whitespace-pre-wrap border-t border-slate-100 px-3 py-2 font-sans text-xs text-slate-700">{String(m.body ?? "")}</pre>
            </details>
          ))}
        </div>
      </div>
    );
  }
  if (records) return <RecordTable rows={records[1] as Record<string, unknown>[]} max={25} />;
  return (
    <KV
      items={Object.entries(payload).map(([k, v]) => [titleCase(k), typeof v === "object" ? <span className="font-mono text-xs">{JSON.stringify(v)}</span> : String(v)])}
    />
  );
}

function ApprovalDetail({ id }: { id: string }) {
  const qc = useQueryClient();
  const { data: a, isLoading } = useQuery({ queryKey: ["approval", id], queryFn: () => api.get<Approval>(`/approvals/${id}`) });
  const [comment, setComment] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  useEffect(() => {
    setEditing(false);
    setComment("");
    setParseError(null);
    if (a) setDraft(JSON.stringify(a.edited_payload ?? a.payload, null, 2));
  }, [a]);

  const decide = useMutation({
    mutationFn: (decision: Decision) => {
      let edited: unknown;
      if (decision === "edit") {
        edited = JSON.parse(draft);
      }
      return api.post<Approval>(`/approvals/${id}/decision`, { decision, comment, edited_payload: edited });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["approval", id] });
    },
  });

  function submit(decision: Decision) {
    if (decision === "edit") {
      try {
        JSON.parse(draft);
        setParseError(null);
      } catch (err) {
        setParseError(`Invalid JSON: ${(err as Error).message}`);
        return;
      }
    }
    if ((decision === "reject" || decision === "request_changes") && !comment.trim()) {
      setParseError("Add a comment so the agent knows what to change.");
      return;
    }
    decide.mutate(decision);
  }

  if (isLoading || !a) return <Spinner />;
  const pending = a.status === "PENDING";
  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={a.status} />
          <RiskBadge level={a.risk_level} />
          <Badge>{titleCase(a.action_type)}</Badge>
          {a.tool_name && <Badge className="bg-blue-50 font-mono text-blue-700 ring-blue-200">{a.tool_name}</Badge>}
        </div>
        <h2 className="mt-2 text-lg font-semibold text-slate-900">{a.title}</h2>
        <p className="mt-1 text-sm text-slate-600">{a.reason}</p>
      </div>

      <KV
        items={[
          ["Project", <Link className="text-brand-600 hover:underline" to={`/projects/${a.project_id}`}>{a.project_name ?? a.project_id}</Link>],
          ["Requested by", a.agent_key],
          ["Required role", titleCase(a.required_role)],
          ["Requested", dateTime(a.created_at)],
          ...(a.decided_at ? ([["Decided", `${dateTime(a.decided_at)}${a.comment ? ` — “${a.comment}”` : ""}`]] as [string, string][]) : []),
        ]}
      />

      {a.policy_refs?.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-800">
            <ShieldAlert className="h-3.5 w-3.5" /> Why this needs approval
          </div>
          <ul className="space-y-1 text-sm text-amber-900">
            {a.policy_refs.map((p, i) => (
              <li key={i}><b>{p.name}</b>{p.description ? ` — ${p.description}` : ""}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{a.edited_payload ? "Edited action" : "Proposed action"}</h3>
          {pending && a.can_decide && (
            <button className="flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline" onClick={() => setEditing((e) => !e)}>
              <Pencil className="h-3 w-3" /> {editing ? "Preview" : "Edit payload"}
            </button>
          )}
        </div>
        {editing ? (
          <Textarea rows={16} value={draft} onChange={(e) => setDraft(e.target.value)} className="font-mono text-xs" spellCheck={false} />
        ) : (
          <PayloadPreview payload={(a.edited_payload ?? a.payload) as Record<string, unknown>} />
        )}
        {!editing && (
          <div className="mt-2">
            <JsonView value={a.edited_payload ?? a.payload} collapsed />
          </div>
        )}
      </div>

      {pending &&
        (a.can_decide ? (
          <div className="space-y-3 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200">
            <Textarea rows={2} placeholder="Comment for the audit log and the agent (required for reject / request changes)" value={comment} onChange={(e) => setComment(e.target.value)} />
            <ErrorBox error={parseError ?? decide.error} />
            <div className="flex flex-wrap gap-2">
              {editing ? (
                <Button variant="success" icon={<Check className="h-4 w-4" />} loading={decide.isPending} onClick={() => submit("edit")}>Approve with edits</Button>
              ) : (
                <Button variant="success" icon={<Check className="h-4 w-4" />} loading={decide.isPending} onClick={() => submit("approve")}>Approve</Button>
              )}
              <Button icon={<MessageSquareWarning className="h-4 w-4" />} loading={decide.isPending} onClick={() => submit("request_changes")}>Request changes</Button>
              <Button variant="danger" icon={<X className="h-4 w-4" />} loading={decide.isPending} onClick={() => submit("reject")}>Reject</Button>
            </div>
          </div>
        ) : (
          <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">Only a {titleCase(a.required_role)} (or an admin) can decide this request.</div>
        ))}
    </div>
  );
}

export default function Approvals() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<"PENDING" | "ALL">("PENDING");
  const { data, isLoading } = useQuery({
    queryKey: ["approvals", "list", status],
    queryFn: () => api.get<Approval[]>(`/approvals?status=${status}`),
    refetchInterval: 4_000,
  });
  const list = data ?? [];
  useEffect(() => {
    if (!id && list.length) navigate(`/approvals/${list[0].id}`, { replace: true });
  }, [id, list, navigate]);

  return (
    <div>
      <PageHeader title="Approval inbox" description="Every high-impact action an agent proposes — external emails, CRM writes, refunds, budget extensions, escalations — waits here for a human decision." />
      <div className="grid gap-6 lg:grid-cols-[400px_1fr]">
        <Card bodyClass="">
          <Tabs
            className="px-3"
            value={status}
            onChange={setStatus}
            tabs={[
              { key: "PENDING", label: "Pending", count: status === "PENDING" ? list.length : undefined },
              { key: "ALL", label: "History" },
            ]}
          />
          {isLoading ? (
            <Spinner />
          ) : list.length === 0 ? (
            <Empty title={status === "PENDING" ? "Inbox zero" : "No approvals yet"}>{status === "PENDING" ? "Agents will ask here before any risky action." : undefined}</Empty>
          ) : (
            <ul className="scrollbar-thin max-h-[70vh] divide-y divide-slate-100 overflow-y-auto">
              {list.map((a) => (
                <li key={a.id}>
                  <Link to={`/approvals/${a.id}`} className={clsx("block px-4 py-3 hover:bg-slate-50", id === a.id && "bg-brand-50/60")}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-slate-900">{a.title}</span>
                      {a.status === "PENDING" ? <RiskBadge level={a.risk_level} /> : <StatusBadge status={a.status} />}
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                      <span className="truncate">{a.project_name}</span>
                      <span>·</span>
                      <span>{a.agent_key}</span>
                      <span className="ml-auto shrink-0">{timeAgo(a.created_at)}</span>
                    </div>
                    {a.status === "PENDING" && !a.can_decide && <div className="mt-1 text-[11px] text-slate-400">Needs {titleCase(a.required_role)}</div>}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>{id ? <ApprovalDetail id={id} /> : <Empty title="Select an approval" />}</Card>
      </div>
    </div>
  );
}
