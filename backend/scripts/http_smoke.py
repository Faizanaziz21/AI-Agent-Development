"""Black-box smoke test against a running deployment (Compose, Kubernetes or a dev server).

Logs in, starts a flagship project from its template, approves every approval it raises,
and waits for it to finish. Exits non-zero if the project does not complete.

    python scripts/http_smoke.py --base http://localhost:8080 --template b2b_sales
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--email", default="admin@sentinel.example")
    ap.add_argument("--password", default="AgentOS!2026")
    ap.add_argument("--template", default="b2b_sales", help="a project template key, or \"support\" to submit a demo ticket")
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args()

    c = httpx.Client(base_url=f"{args.base.rstrip('/')}/api/v1", timeout=30)
    token = c.post("/auth/login", json={"email": args.email, "password": args.password}).raise_for_status().json()["access_token"]
    c.headers["Authorization"] = f"Bearer {token}"

    catalog = c.get("/projects/templates").raise_for_status().json()
    if args.template == "support":
        pid = c.post("/support/tickets", json=catalog["demo_tickets"][0]).raise_for_status().json()["project_id"]
    else:
        tpl = next(t for t in catalog["templates"] if t["key"] == args.template)
        pid = c.post("/projects", json={"name": f"smoke {args.template} {int(time.time())}", "objective": tpl["objective"],
                                        "template": args.template, "parameters": tpl["parameters"],
                                        "budget_usd": tpl["budget_usd"]}).raise_for_status().json()["id"]
    print(f"project {pid} started")

    start, approved, last = time.monotonic(), 0, ""
    while time.monotonic() - start < args.timeout:
        p = c.get(f"/projects/{pid}").raise_for_status().json()
        for a in c.get("/approvals", params={"status": "PENDING", "project_id": pid}).raise_for_status().json():
            c.post(f"/approvals/{a['id']}/decision", json={"decision": "approve", "comment": "smoke test"}).raise_for_status()
            approved += 1
            print(f"  approved: {a['title']}")
        line = f"  {p['status']} {p.get('progress', 0):.0%} spent ${p['spent_usd']:.4f}"
        if line != last:
            print(line)
            last = line
        if p["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    graph = c.get(f"/projects/{pid}/graph").raise_for_status().json()
    agents = sorted({n["agent_key"] for n in graph["nodes"]})
    print(f"status={p['status']} tasks={len(graph['nodes'])} agents={len(agents)} approvals={approved} "
          f"elapsed={time.monotonic() - start:.1f}s cost=${p['spent_usd']:.4f}")
    print("agents:", ", ".join(agents))
    sys.exit(0 if p["status"] == "COMPLETED" else 1)


if __name__ == "__main__":
    main()
