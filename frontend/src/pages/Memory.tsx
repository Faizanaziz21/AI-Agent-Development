import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Network, Plus, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { EntityGraph } from "../components/DagView";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, JsonView, KV, Modal, PageHeader, Select, Spinner, Stat, Tabs, Textarea } from "../components/ui";
import { api, qs } from "../lib/api";
import { useAuth } from "../lib/auth";
import { pct, timeAgo, titleCase } from "../lib/format";
import type { Entity, MemoryItem, Relationship } from "../lib/types";

const TRUST_STYLE: Record<string, string> = {
  unverified: "bg-slate-100 text-slate-600 ring-slate-200",
  verified: "bg-sky-50 text-sky-700 ring-sky-200",
  approved: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  rejected: "bg-rose-50 text-rose-700 ring-rose-200",
};

type Summary = { items: { layer: string; trust_level: string; count: number }[]; entities: Record<string, number>; relationships: number };
type SearchHit = Pick<MemoryItem, "id" | "layer" | "kind" | "content" | "trust_level" | "source_type" | "source_ref" | "created_at"> & { agent: string | null; score: number };

export default function Memory() {
  const [tab, setTab] = useState<"items" | "graph">("items");
  const summary = useQuery({ queryKey: ["memory", "summary"], queryFn: () => api.get<Summary>("/memory/summary") });
  const count = (pred: (r: Summary["items"][number]) => boolean) => (summary.data?.items ?? []).filter(pred).reduce((a, r) => a + r.count, 0);
  const entityTotal = Object.values(summary.data?.entities ?? {}).reduce((a, n) => a + n, 0);

  return (
    <div>
      <PageHeader
        title="Memory explorer"
        description="Session memory is scoped to a project; long-term memory is shared across projects. AI-written memory starts unverified and only becomes organisational knowledge once QA verifies it or a human approves it."
      />
      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Session items" value={count((r) => r.layer === "session")} icon={<Brain className="h-4 w-4" />} tone="sky" />
        <Stat label="Long-term items" value={count((r) => r.layer === "long_term")} icon={<Brain className="h-4 w-4" />} tone="brand" />
        <Stat label="Awaiting review" value={count((r) => r.trust_level === "unverified")} hint="Unverified AI-written memory" tone="amber" icon={<Search className="h-4 w-4" />} />
        <Stat label="Entities / relationships" value={`${entityTotal} / ${summary.data?.relationships ?? 0}`} icon={<Network className="h-4 w-4" />} tone="emerald" />
      </div>
      <Tabs className="mb-5" value={tab} onChange={setTab} tabs={[{ key: "items", label: "Memory items" }, { key: "graph", label: "Entity graph", count: entityTotal }]} />
      {tab === "items" ? <Items /> : <Graph />}
    </div>
  );
}

function Items() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [layer, setLayer] = useState("");
  const [trust, setTrust] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const items = useQuery({
    queryKey: ["memory", "items", layer, trust, query],
    queryFn: () => api.get<(MemoryItem | SearchHit)[]>(`/memory/items${qs({ layer, trust_level: trust, q: query, limit: 200 })}`),
  });
  const review = useMutation({
    mutationFn: ({ id, action, promote }: { id: string; action: "approve" | "reject"; promote?: boolean }) =>
      api.post(`/memory/items/${id}/${action}`, action === "approve" ? { promote: !!promote } : {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory"] }),
  });
  const reviewer = can("memory.approve");

  return (
    <Card
      title={query ? `Semantic matches for “${query}”` : "Memory items"}
      actions={reviewer && <Button size="sm" variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setAdding(true)}>Add knowledge</Button>}
      bodyClass=""
    >
      <form
        className="flex flex-wrap gap-2 border-b border-slate-100 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(q.trim());
        }}
      >
        <Input aria-label="Semantic search" placeholder="Semantic search, e.g. “pricing objections from fintech buyers”" value={q} onChange={(e) => setQ(e.target.value)} className="min-w-64 flex-1" />
        <Select aria-label="Layer" value={layer} onChange={(e) => setLayer(e.target.value)} className="w-40" disabled={!!query}>
          <option value="">All layers</option>
          <option value="session">Session</option>
          <option value="long_term">Long-term</option>
        </Select>
        <Select aria-label="Trust level" value={trust} onChange={(e) => setTrust(e.target.value)} className="w-40" disabled={!!query}>
          <option value="">Any trust level</option>
          {Object.keys(TRUST_STYLE).map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
        </Select>
        <Button type="submit" icon={<Search className="h-4 w-4" />}>Search</Button>
        {query && <Button variant="ghost" onClick={() => { setQ(""); setQuery(""); }}>Clear</Button>}
      </form>
      <div className="px-3 pt-3"><ErrorBox error={items.error ?? review.error} /></div>
      {items.isLoading ? <Spinner /> : (items.data ?? []).length === 0 ? <Empty title="No memory items match" /> : (
        <ul className="divide-y divide-slate-100">
          {items.data!.map((m) => {
            const agent = "agent" in m ? m.agent : (m as MemoryItem).created_by_agent;
            return (
              <li key={m.id} className="flex gap-4 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs">
                    <Badge className={TRUST_STYLE[m.trust_level]}>{m.trust_level}</Badge>
                    <Badge>{m.layer === "long_term" ? "long-term" : m.layer}</Badge>
                    <Badge>{m.kind}</Badge>
                    <span className="text-slate-500">
                      {agent ? `by ${titleCase(agent)}` : m.source_type === "human" ? "by a human" : titleCase(m.source_type)} · {timeAgo(m.created_at)}
                    </span>
                    {"score" in m && m.score != null && <span className="text-slate-400">relevance {m.score.toFixed(3)}</span>}
                    {"confidence" in m && <span className="text-slate-400">confidence {pct(m.confidence)}</span>}
                  </div>
                  <p className="whitespace-pre-line text-sm text-slate-800">{m.content}</p>
                  {m.source_ref && <div className="mt-1 truncate font-mono text-[11px] text-slate-400">{m.source_ref}</div>}
                </div>
                {reviewer && (m.trust_level === "unverified" || m.trust_level === "verified") && (
                  <div className="flex shrink-0 flex-col gap-1.5">
                    <Button size="sm" variant="success" icon={<Check className="h-3.5 w-3.5" />} onClick={() => review.mutate({ id: m.id, action: "approve", promote: m.layer === "session" })}
                      title={m.layer === "session" ? "Approve and promote to long-term memory" : "Approve"}>
                      {m.layer === "session" ? "Approve & promote" : "Approve"}
                    </Button>
                    <Button size="sm" variant="ghost" icon={<X className="h-3.5 w-3.5" />} onClick={() => review.mutate({ id: m.id, action: "reject" })}>Reject</Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <AddMemory open={adding} onClose={() => setAdding(false)} />
    </Card>
  );
}

function AddMemory({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState({ content: "", kind: "fact", key: "" });
  const save = useMutation({
    mutationFn: () => api.post("/memory/items", d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory"] });
      setD({ content: "", kind: "fact", key: "" });
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="Add organisational knowledge"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={d.content.trim().length < 3} onClick={() => save.mutate()}>Save as approved</Button></>}>
      <div className="space-y-3">
        <p className="text-xs text-slate-500">Human-written memory is stored in the long-term layer as approved and is retrieved by every agent.</p>
        <Field label="Content"><Textarea rows={4} value={d.content} onChange={(e) => setD({ ...d, content: e.target.value })} placeholder="Our ICP excludes companies under 50 employees." /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Kind">
            <Select value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value })}>
              {["fact", "preference", "lesson", "procedure", "insight"].map((k) => <option key={k} value={k}>{titleCase(k)}</option>)}
            </Select>
          </Field>
          <Field label="Key (optional)"><Input value={d.key} onChange={(e) => setD({ ...d, key: e.target.value })} placeholder="icp.min_employees" /></Field>
        </div>
        <ErrorBox error={save.error} />
      </div>
    </Modal>
  );
}

function Graph() {
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const data = useQuery({
    queryKey: ["memory", "entities", type, q],
    queryFn: () => api.get<{ entities: Entity[]; relationships: Relationship[] }>(`/memory/entities${qs({ type, q, limit: 300 })}`),
  });
  const types = useMemo(() => [...new Set((data.data?.entities ?? []).map((e) => e.type))].sort(), [data.data]);
  const entity = data.data?.entities.find((e) => e.id === selected) ?? null;
  const names = new Map((data.data?.entities ?? []).map((e) => [e.id, e.name]));
  const related = (data.data?.relationships ?? []).filter((r) => r.source_entity_id === selected || r.target_entity_id === selected);

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
      <Card
        title="Entity graph"
        subtitle="Companies, people, products and their relationships extracted by agents, with provenance. Dashed borders are unverified."
        actions={
          <div className="flex gap-2">
            <Input aria-label="Filter entities" placeholder="Filter by name" value={q} onChange={(e) => setQ(e.target.value)} className="w-44" />
            <Select aria-label="Entity type" value={type} onChange={(e) => setType(e.target.value)} className="w-36">
              <option value="">All types</option>
              {types.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
            </Select>
          </div>
        }
      >
        {data.isLoading ? <Spinner /> : data.error ? <ErrorBox error={data.error} /> : (data.data?.entities.length ?? 0) === 0 ? (
          <Empty title="No entities yet" icon={<Network className="h-5 w-5" />}>Run the B2B sales demo; agents record companies, contacts and relationships as they research.</Empty>
        ) : (
          <EntityGraph entities={data.data!.entities} relationships={data.data!.relationships} onSelect={setSelected} />
        )}
      </Card>
      <Card title={entity ? entity.name : "Entity details"} subtitle={entity ? titleCase(entity.type) : "Select a node"}>
        {!entity ? <p className="text-sm text-slate-500">Click an entity in the graph to see its attributes, provenance and relationships.</p> : (
          <div className="space-y-4">
            <KV items={[
              ["Trust", <Badge key="t" className={TRUST_STYLE[entity.trust_level]}>{entity.trust_level}</Badge>],
              ["First seen", timeAgo(entity.created_at)],
            ]} />
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Attributes</h4>
              <KV items={Object.entries(entity.attributes).map(([k, v]) => [titleCase(k), typeof v === "object" ? JSON.stringify(v) : String(v)])} />
            </div>
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Relationships</h4>
              {related.length === 0 ? <p className="text-xs text-slate-500">None recorded.</p> : (
                <ul className="space-y-1 text-xs">
                  {related.map((r) => (
                    <li key={r.id}>
                      <span className="font-medium">{names.get(r.source_entity_id) ?? "?"}</span>
                      <span className="mx-1 text-slate-500">{titleCase(r.type)}</span>
                      <span className="font-medium">{names.get(r.target_entity_id) ?? "?"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Provenance</h4>
              <JsonView value={entity.provenance} collapsed />
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
