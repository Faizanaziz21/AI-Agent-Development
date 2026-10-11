import { ArrowRight, GitBranch, ShieldCheck, Workflow } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button, ErrorBox, Field, Input } from "../components/ui";
import { useAuth } from "../lib/auth";

const DEMO_USERS = [
  { email: "admin@sentinel.example", role: "Owner — full access" },
  { email: "ops@sentinel.example", role: "Operator — runs projects" },
  { email: "approver@sentinel.example", role: "Approver — signs off actions" },
  { email: "finance@sentinel.example", role: "Finance — refunds & budgets" },
  { email: "compliance@sentinel.example", role: "Compliance — audit & policy" },
  { email: "viewer@sentinel.example", role: "Viewer — read only" },
];

const FEATURES = [
  { icon: <Workflow className="h-4 w-4" />, text: "Task DAGs with parallel agents, delegation and QA loops" },
  { icon: <ShieldCheck className="h-4 w-4" />, text: "Policy-gated tools, approvals and prompt-injection defence" },
  { icon: <GitBranch className="h-4 w-4" />, text: "Multi-provider routing with budgets, fallback and replay" },
];

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("admin@sentinel.example");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="relative hidden overflow-hidden bg-gradient-to-br from-brand-700 via-brand-600 to-indigo-500 p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="h-10 w-10 rounded-lg ring-2 ring-white/30" />
          <span className="text-lg font-semibold">AgentOS</span>
        </div>
        <div className="max-w-md space-y-6">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">Run a governed digital workforce.</h1>
          <p className="text-brand-100">
            Supervisors plan, specialists execute, critics review, and humans stay in control of every high-impact action — with full cost, audit and replay.
          </p>
          <ul className="space-y-3 text-sm">
            {FEATURES.map((f) => (
              <li key={f.text} className="flex items-center gap-3">
                <span className="rounded-md bg-white/15 p-1.5">{f.icon}</span>
                {f.text}
              </li>
            ))}
          </ul>
        </div>
        <div className="text-xs text-brand-200">Demo tenant: Sentinel Devices Ltd</div>
      </div>

      <div className="flex items-center justify-center p-6 sm:p-12">
        <div className="w-full max-w-sm">
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Sign in</h2>
          <p className="mt-1 text-sm text-slate-500">
            Use a seeded demo account. All personas share the demo password <code className="rounded bg-slate-100 px-1">AgentOS!2026</code>.
          </p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            <Field label="Email">
              <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            <Field label="Password">
              <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </Field>
            <ErrorBox error={error} />
            <Button type="submit" variant="primary" className="w-full" loading={busy} icon={<ArrowRight className="h-4 w-4" />}>
              Sign in
            </Button>
          </form>
          <div className="mt-8">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Demo personas</div>
            <div className="divide-y divide-slate-100 rounded-lg ring-1 ring-slate-200">
              {DEMO_USERS.map((u) => (
                <button key={u.email} type="button" onClick={() => setEmail(u.email)} className="flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-slate-50">
                  <span className="font-medium text-slate-700">{u.email}</span>
                  <span className="text-slate-400">{u.role}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
