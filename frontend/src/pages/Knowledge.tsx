import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Lock, MessageSquareText, Search, ShieldAlert, Trash2, Upload } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Drawer, Empty, ErrorBox, Field, Input, KV, Modal, PageHeader, Select, Spinner, StatusBadge, Table, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { bytes, pct, timeAgo, titleCase, usd } from "../lib/format";
import type { KnowledgeSource, Passage } from "../lib/types";

const CLASS_STYLE: Record<string, string> = {
  public: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  internal: "bg-sky-50 text-sky-700 ring-sky-200",
  confidential: "bg-amber-50 text-amber-700 ring-amber-200",
  legal: "bg-rose-50 text-rose-700 ring-rose-200",
};

type Chunk = { id: string; ordinal: number; text: string; heading: string; token_count: number; injection_risk: number };
type Answer = { question: string; answer: string; citations: string[]; confidence: number; passages: Passage[]; model: string; provider: string; cost_usd: number };

export default function Knowledge() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const sources = useQuery({ queryKey: ["knowledge"], queryFn: () => api.get<KnowledgeSource[]>("/knowledge/sources") });
  const [uploading, setUploading] = useState(false);
  const [open, setOpen] = useState<KnowledgeSource | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/knowledge/sources/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }),
  });
  const totalChunks = (sources.data ?? []).reduce((a, s) => a + s.chunk_count, 0);

  return (
    <div>
      <PageHeader
        title="Knowledge base"
        description="Documents are parsed, chunked, embedded and indexed for hybrid (vector + BM25) search. Retrieved text is always passed to agents as untrusted data, and classification gates who can see what."
        actions={can("knowledge.write") && <Button variant="primary" icon={<Upload className="h-4 w-4" />} onClick={() => setUploading(true)}>Upload document</Button>}
      />
      <div className="grid gap-6 xl:grid-cols-[1fr_460px]">
        <Card title="Sources" subtitle={`${sources.data?.length ?? 0} documents · ${totalChunks} chunks`} bodyClass="">
          {sources.isLoading ? <Spinner /> : (sources.data ?? []).length === 0 ? (
            <Empty title="No documents yet" icon={<FileText className="h-5 w-5" />}>Upload PDFs, Word, HTML, Markdown, CSV or JSON files.</Empty>
          ) : (
            <Table head={["Document", "Classification", "Status", "Chunks", "Size", "Added", ""]}>
              {sources.data!.map((s) => (
                <tr key={s.id} className="align-top">
                  <td className="max-w-sm px-3 py-2.5">
                    <button
                      disabled={s.restricted}
                      onClick={() => setOpen(s)}
                      className="flex items-center gap-1.5 text-left font-medium text-slate-900 hover:text-brand-700 disabled:cursor-not-allowed disabled:text-slate-500"
                    >
                      {s.restricted && <Lock className="h-3.5 w-3.5" />}
                      <span className="truncate">{s.name}</span>
                    </button>
                    <div className="truncate text-xs text-slate-500">{s.filename} · {titleCase(s.source_type)}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {s.tags.map((t) => <Badge key={t}>{t}</Badge>)}
                      {s.injection_flags > 0 && (
                        <Badge className="bg-rose-50 text-rose-700 ring-rose-200" title="Chunks containing instruction-like text; they are quarantined as data">
                          <ShieldAlert className="h-3 w-3" /> {s.injection_flags} injection flag{s.injection_flags > 1 ? "s" : ""}
                        </Badge>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2.5"><Badge className={CLASS_STYLE[s.classification]}>{s.classification}</Badge></td>
                  <td className="px-3 py-2.5">
                    <StatusBadge status={s.status} />
                    {s.error && <div className="mt-1 max-w-[12rem] text-xs text-rose-600">{s.error}</div>}
                  </td>
                  <td className="px-3 py-2.5 text-xs tabular-nums">{s.chunk_count}</td>
                  <td className="px-3 py-2.5 text-xs text-slate-600">{bytes(s.size_bytes)}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-xs text-slate-500">{timeAgo(s.created_at)}</td>
                  <td className="px-3 py-2.5 text-right">
                    {can("knowledge.write") && (
                      <Button size="sm" variant="ghost" aria-label={`Delete ${s.name}`} icon={<Trash2 className="h-3.5 w-3.5" />}
                        onClick={() => confirm(`Delete "${s.name}" and its index?`) && remove.mutate(s.id)} />
                    )}
                  </td>
                </tr>
              ))}
            </Table>
          )}
          <div className="px-4 pb-3"><ErrorBox error={remove.error} /></div>
        </Card>
        <SearchPanel />
      </div>
      <UploadModal open={uploading} onClose={() => setUploading(false)} />
      <SourceDrawer source={open} onClose={() => setOpen(null)} />
    </div>
  );
}

function SearchPanel() {
  const [tab, setTab] = useState<"ask" | "search">("ask");
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"hybrid" | "semantic" | "keyword">("hybrid");
  const ask = useMutation({ mutationFn: (question: string) => api.post<Answer>("/knowledge/ask", { question, mode }) });
  const search = useMutation({ mutationFn: (query: string) => api.post<{ results: Passage[] }>("/knowledge/search", { query, mode, k: 8 }) });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (q.trim().length < 3) return;
    if (tab === "ask") ask.mutate(q.trim());
    else search.mutate(q.trim());
  };
  return (
    <Card title="Ask & search" bodyClass="p-0">
      <Tabs className="px-4 pt-2" value={tab} onChange={setTab} tabs={[{ key: "ask", label: "Ask with citations" }, { key: "search", label: "Raw retrieval" }]} />
      <form onSubmit={submit} className="flex gap-2 p-4">
        <Input
          aria-label="Question"
          placeholder={tab === "ask" ? "What is the refund policy for annual plans?" : "SSO configuration"}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select aria-label="Retrieval mode" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} className="w-32">
          <option value="hybrid">Hybrid</option>
          <option value="semantic">Semantic</option>
          <option value="keyword">Keyword</option>
        </Select>
        <Button type="submit" variant="primary" loading={ask.isPending || search.isPending} icon={tab === "ask" ? <MessageSquareText className="h-4 w-4" /> : <Search className="h-4 w-4" />} />
      </form>
      <div className="space-y-3 px-4 pb-4">
        <ErrorBox error={tab === "ask" ? ask.error : search.error} />
        {tab === "ask" && ask.data && (
          <div className="space-y-3">
            <div className="rounded-lg bg-brand-50/60 p-3 text-sm leading-relaxed text-slate-800 ring-1 ring-brand-100">
              <CitedText text={ask.data.answer} />
              <div className="mt-2 flex flex-wrap gap-x-3 text-[11px] text-slate-500">
                <span>confidence {pct(ask.data.confidence)}</span>
                <span>{ask.data.provider}/{ask.data.model}</span>
                <span>{usd(ask.data.cost_usd, 5)}</span>
              </div>
            </div>
            <PassageList passages={ask.data.passages} cited={ask.data.citations} />
          </div>
        )}
        {tab === "search" && search.data && (search.data.results.length === 0 ? <Empty title="No matching passages" /> : <PassageList passages={search.data.results} />)}
      </div>
    </Card>
  );
}

function CitedText({ text }: { text: string }) {
  return (
    <p>
      {text.split(/(\[S\d+\])/g).map((part, i) =>
        /^\[S\d+\]$/.test(part) ? (
          <a key={i} href={`#passage-${part.slice(1, -1)}`} className="mx-0.5 rounded bg-brand-100 px-1 font-mono text-[11px] text-brand-700 hover:bg-brand-200">{part.slice(1, -1)}</a>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </p>
  );
}

function PassageList({ passages, cited }: { passages: Passage[]; cited?: string[] }) {
  return (
    <ol className="space-y-2">
      {passages.map((p) => (
        <li key={p.chunk_id} id={`passage-${p.id}`} className={`rounded-lg border p-3 text-xs ${cited?.includes(p.id) ? "border-brand-200 bg-white" : "border-slate-200 bg-slate-50/60"}`}>
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span className="rounded bg-slate-900 px-1 font-mono text-[10px] text-white">{p.id}</span>
            <span className="font-medium text-slate-800">{p.source}</span>
            {p.section && <span className="text-slate-500">› {p.section}</span>}
            <Badge className={CLASS_STYLE[p.classification]}>{p.classification}</Badge>
            {p.injection_risk >= 0.5 && <Badge className="bg-rose-50 text-rose-700 ring-rose-200">injection risk {pct(p.injection_risk)}</Badge>}
          </div>
          <p className="line-clamp-4 whitespace-pre-line text-slate-600">{p.text}</p>
          <div className="mt-1 text-[10px] text-slate-400">
            semantic rank {p.semantic_rank ?? "—"} · keyword rank {p.keyword_rank ?? "—"}{p.score != null && ` · fused ${p.score.toFixed(4)}`}
          </div>
        </li>
      ))}
    </ol>
  );
}

function UploadModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState({ name: "", source_type: "document", classification: "internal", tags: "" });
  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append("file", file!);
      Object.entries(meta).forEach(([k, v]) => f.append(k, v));
      return api.upload<KnowledgeSource>("/knowledge/sources", f);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge"] });
      setFile(null);
      setMeta({ name: "", source_type: "document", classification: "internal", tags: "" });
      onClose();
    },
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Upload document"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!file} loading={upload.isPending} onClick={() => upload.mutate()}>Upload & index</Button></>}
    >
      <div className="space-y-3">
        <Field label="File" hint="PDF, DOCX, HTML, Markdown, TXT, CSV or JSON — up to 20 MB.">
          <input
            type="file"
            accept=".pdf,.docx,.html,.htm,.csv,.md,.markdown,.txt,.json"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700"
          />
        </Field>
        <Field label="Display name"><Input placeholder={file?.name ?? "Defaults to the file name"} value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Type">
            <Select value={meta.source_type} onChange={(e) => setMeta({ ...meta, source_type: e.target.value })}>
              {["document", "policy", "product_docs", "contract", "playbook", "faq", "dataset"].map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
            </Select>
          </Field>
          <Field label="Classification">
            <Select value={meta.classification} onChange={(e) => setMeta({ ...meta, classification: e.target.value })}>
              {Object.keys(CLASS_STYLE).map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Tags" hint="Comma separated"><Input value={meta.tags} onChange={(e) => setMeta({ ...meta, tags: e.target.value })} /></Field>
        <ErrorBox error={upload.error} />
      </div>
    </Modal>
  );
}

function SourceDrawer({ source, onClose }: { source: KnowledgeSource | null; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["knowledge", source?.id],
    queryFn: () => api.get<KnowledgeSource & { chunks: Chunk[] }>(`/knowledge/sources/${source!.id}`),
    enabled: !!source,
  });
  return (
    <Drawer open={!!source} onClose={onClose} title={source?.name ?? ""} subtitle={source?.filename}>
      <div className="space-y-4 p-5">
        {source && (
          <KV items={[
            ["Classification", <Badge key="c" className={CLASS_STYLE[source.classification]}>{source.classification}</Badge>],
            ["Status", <StatusBadge key="s" status={source.status} />],
            ["Type", titleCase(source.source_type)],
            ["Size", bytes(source.size_bytes)],
            ["Chunks", source.chunk_count],
          ]} />
        )}
        {detail.isLoading ? <Spinner /> : detail.error ? <ErrorBox error={detail.error} /> : (
          <ol className="space-y-2">
            {detail.data?.chunks.map((c) => (
              <li key={c.id} className={`rounded-lg border p-3 text-xs ${c.injection_risk >= 0.5 ? "border-rose-200 bg-rose-50/50" : "border-slate-200"}`}>
                <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-500">
                  <span className="font-mono">#{c.ordinal}</span>
                  {c.heading && <span className="font-medium text-slate-700">{c.heading}</span>}
                  <span>{c.token_count} tokens</span>
                  {c.injection_risk >= 0.5 && <Badge className="bg-rose-50 text-rose-700 ring-rose-200">injection risk {pct(c.injection_risk)}</Badge>}
                </div>
                <p className="whitespace-pre-line text-slate-700">{c.text}</p>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Drawer>
  );
}
