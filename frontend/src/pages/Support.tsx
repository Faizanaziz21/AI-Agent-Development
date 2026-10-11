import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Webhook } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge, Button, Card, Empty, ErrorBox, Field, Input, PageHeader, RiskBadge, Spinner, StatusBadge, Table, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { pct, timeAgo, titleCase, truncate } from "../lib/format";
import type { OrgDetail, SupportTicket, Templates } from "../lib/types";

export default function Support() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const tickets = useQuery({ queryKey: ["tickets"], queryFn: () => api.get<SupportTicket[]>("/tickets"), refetchInterval: 4_000 });
  const tpl = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Templates>("/projects/templates"), staleTime: 300_000 });
  const org = useQuery({ queryKey: ["org"], queryFn: () => api.get<OrgDetail>("/org") });
  const [form, setForm] = useState({ customer_email: "", subject: "", body: "" });
  const submit = useMutation({
    mutationFn: (t: typeof form) => api.post<{ project_id: string; ticket_id: string }>("/support/tickets", t),
    onSuccess: () => {
      setForm({ customer_email: "", subject: "", body: "" });
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
  const submitAll = useMutation({
    mutationFn: async () => {
      for (const t of tpl.data?.demo_tickets ?? []) await api.post("/support/tickets", t);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tickets"] }),
  });

  return (
    <div>
      <PageHeader
        title="Autonomous support desk"
        description="Each inbound ticket becomes a project: triage, account lookup, documentation retrieval, solution, risk evaluation, reply and close-out — with refunds and legal threats routed to humans."
        actions={
          can("project.write") && (
            <Button variant="primary" loading={submitAll.isPending} onClick={() => submitAll.mutate()}>
              Submit the 3 demo tickets
            </Button>
          )
        }
      />
      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <Card title="Tickets" bodyClass="">
          {tickets.isLoading ? (
            <Spinner />
          ) : (tickets.data ?? []).length === 0 ? (
            <Empty title="No tickets yet">Submit a ticket here, or POST a signed webhook from your helpdesk.</Empty>
          ) : (
            <Table head={["Ticket", "Customer", "Category", "Risk", "Status", "Received", ""]}>
              {tickets.data!.map((t) => {
                const injection = Number(t.attributes?.injection_risk ?? 0);
                return (
                  <tr key={t.id} className="align-top">
                    <td className="max-w-md px-3 py-2.5">
                      <div className="truncate font-medium text-slate-900">{t.subject}</div>
                      <div className="truncate text-xs text-slate-500">{t.summary || truncate(t.body, 110)}</div>
                      {injection >= 0.5 && <Badge className="mt-1 bg-rose-50 text-rose-700 ring-rose-200">injection risk {pct(injection)}</Badge>}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-slate-600">{t.customer_email}</td>
                    <td className="px-3 py-2.5 text-xs">
                      {t.category ? titleCase(t.category) : <span className="text-slate-400">triaging…</span>}
                      {t.product_area && <div className="text-slate-400">{t.product_area}</div>}
                    </td>
                    <td className="px-3 py-2.5">{t.risk_level ? <RiskBadge level={t.risk_level} /> : <span className="text-xs text-slate-400">—</span>}</td>
                    <td className="px-3 py-2.5"><StatusBadge status={t.status} /></td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-xs text-slate-500">{timeAgo(t.created_at)}</td>
                    <td className="px-3 py-2.5 text-right">
                      {t.project_id && <Link to={`/projects/${t.project_id}`} className="text-xs font-medium text-brand-600 hover:underline">Watch agents</Link>}
                    </td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Card>

        <div className="space-y-6">
          {can("project.write") && (
            <Card title="New ticket">
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  submit.mutate(form);
                }}
              >
                <div className="flex flex-wrap gap-1.5">
                  {tpl.data?.demo_tickets.map((t, i) => (
                    <button key={i} type="button" onClick={() => setForm(t)} className="rounded-md bg-slate-100 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-200">
                      Example {i + 1}
                    </button>
                  ))}
                </div>
                <Field label="Customer email">
                  <Input type="email" required value={form.customer_email} onChange={(e) => setForm({ ...form, customer_email: e.target.value })} />
                </Field>
                <Field label="Subject">
                  <Input required minLength={3} value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
                </Field>
                <Field label="Message">
                  <Textarea required minLength={5} rows={5} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
                </Field>
                <ErrorBox error={submit.error ?? submitAll.error} />
                {submit.data && (
                  <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                    Ticket accepted. <Link className="font-medium underline" to={`/projects/${submit.data.project_id}`}>Watch the support team work →</Link>
                  </div>
                )}
                <Button type="submit" variant="primary" icon={<Send className="h-4 w-4" />} loading={submit.isPending} className="w-full">
                  Submit ticket
                </Button>
              </form>
            </Card>
          )}
          <Card title="Helpdesk integration" actions={<Webhook className="h-4 w-4 text-slate-400" />}>
            <p className="text-xs text-slate-600">Send tickets from Zendesk, Freshdesk or an email gateway. Requests are signed with HMAC-SHA256 over <code>{"{timestamp}.{body}"}</code> and rejected if older than 5 minutes.</p>
            <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{`POST ${org.data?.webhook_url ?? "/api/v1/webhooks/support/<org>"}
X-AgentOS-Timestamp: <unix seconds>
X-AgentOS-Signature: sha256=<hex hmac>

{"external_id": "ZD-1042",
 "customer_email": "…", "subject": "…", "body": "…"}`}</pre>
          </Card>
        </div>
      </div>
    </div>
  );
}
