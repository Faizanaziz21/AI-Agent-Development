"""In-process load test: many concurrent projects through the real orchestrator, worker pool and model gateway.

    python scripts/load_test.py --projects 40 --workers 16 --mix support:3,b2b_sales:1 --chaos 0.1 --out load-report.json

Approvals are auto-decided by a simulated human so throughput measures the platform, not reviewers. With --chaos the
provider, tool and worker-crash fault injectors are enabled per project to exercise retries, fallback and recovery.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_db = os.path.join(tempfile.mkdtemp(prefix="agentos-load-"), "load.db")
os.environ.setdefault("AGENTOS_DATABASE_URL", f"sqlite+aiosqlite:///{_db}")
os.environ.setdefault("AGENTOS_LOCAL_MODEL_LATENCY_MS", "20")
os.environ.setdefault("AGENTOS_EMBEDDED_WORKERS", "0")
os.environ.setdefault("AGENTOS_LOG_LEVEL", "ERROR")

from sqlalchemy import func, select  # noqa: E402

from app.core.db import create_all, init_engine, session_scope  # noqa: E402
from app.core.security import Principal  # noqa: E402
from app.models import Approval, EventRecord, ModelUsage, Organization, Project, Task, ToolCall, User  # noqa: E402
from app.seed.seed import seed_all  # noqa: E402
from app.services import approvals, orchestrator  # noqa: E402
from app.services.projects import DEMO_TICKETS, TEMPLATES, create_project, create_support_ticket  # noqa: E402
from app.services.queue import get_queue  # noqa: E402
from app.services.worker import WorkerPool  # noqa: E402

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))], 3)


def parse_mix(mix: str) -> list[str]:
    out: list[str] = []
    for part in mix.split(","):
        name, _, weight = part.partition(":")
        if name not in ("support", *TEMPLATES):
            raise SystemExit(f"unknown template in --mix: {name}")
        out += [name] * int(weight or 1)
    return out


async def create(kind: str, i: int, org_id: str, user_id: str, chaos: dict) -> str:
    async with session_scope() as s:
        if kind == "support":
            p, _ = await create_support_ticket(s, org_id=org_id, user_id=user_id, chaos=chaos, **DEMO_TICKETS[i % len(DEMO_TICKETS)])
        else:
            t = TEMPLATES[kind]
            p = await create_project(s, org_id=org_id, user_id=user_id, name=f"{t['name']} #{i}", objective=t["objective"],
                                     parameters=t["parameters"], template=kind, budget_usd=t["budget_usd"], chaos=chaos)
        return p.id


async def approver_loop(pids: list[str], humans: dict[str, Principal], stop: asyncio.Event, think_s: float) -> int:
    decided = 0
    while not stop.is_set():
        ids = []
        async with session_scope() as s:
            for a in (await s.execute(select(Approval).where(Approval.project_id.in_(pids), Approval.status == "PENDING"))).scalars():
                who = humans.get(a.required_role) or humans["owner"]
                await approvals.decide(s, a, who, "approve", comment="load-test auto approval")
                ids.append(a.id)
        for aid in ids:
            await orchestrator.apply_approval(aid)
        decided += len(ids)
        await asyncio.sleep(think_s)
    return decided


async def run(args: argparse.Namespace) -> dict:
    init_engine()
    await create_all()
    async with session_scope() as s:
        await seed_all(s)
        org = (await s.execute(select(Organization).where(Organization.slug == "sentinel"))).scalar_one()
        users = list((await s.execute(select(User).where(User.org_id == org.id))).scalars())
    humans = {u.role: Principal(u.id, org.id, u.role, u.email, u.name) for u in users}
    owner = humans["owner"]
    chaos = {"provider_failure_rate": args.chaos, "tool_failure_rate": args.chaos / 2, "malformed_output_rate": args.chaos / 2,
             "worker_crash_rate": args.chaos / 4} if args.chaos else {}

    pool = WorkerPool(args.workers, name="load", recovery_interval=1.0)
    await pool.start()
    kinds = parse_mix(args.mix)
    t0 = time.monotonic()
    pids, started = [], {}
    for i in range(args.projects):
        pid = await create(kinds[i % len(kinds)], i, org.id, owner.user_id, chaos)
        await orchestrator.start_project(pid, owner.user_id)
        pids.append(pid)
        started[pid] = time.monotonic()
        if args.arrival_ms:
            await asyncio.sleep(args.arrival_ms / 1000)

    stop = asyncio.Event()
    approver = asyncio.create_task(approver_loop(pids, humans, stop, args.approval_think_s))
    finished: dict[str, float] = {}
    depth_samples: list[int] = []
    deadline = t0 + args.timeout
    while time.monotonic() < deadline and len(finished) < len(pids):
        depth_samples.append(await get_queue().depth())
        async with session_scope() as s:
            for pid, status in (await s.execute(select(Project.id, Project.status).where(Project.id.in_(pids)))).all():
                if status in TERMINAL and pid not in finished:
                    finished[pid] = time.monotonic()
        if args.progress:
            print(f"\r{len(finished)}/{len(pids)} projects done, queue depth {depth_samples[-1]:4d}, busy workers {pool.busy:3d}",
                  end="", file=sys.stderr, flush=True)
        await asyncio.sleep(0.5)
    wall = time.monotonic() - t0
    stop.set()
    decided = await approver
    await pool.stop()
    if args.progress:
        print(file=sys.stderr)

    async with session_scope() as s:
        statuses = dict((await s.execute(select(Project.status, func.count()).where(Project.id.in_(pids)).group_by(Project.status))).all())
        tasks = list((await s.execute(select(Task).where(Task.project_id.in_(pids)))).scalars())
        tool_stats = dict((await s.execute(select(ToolCall.status, func.count()).where(ToolCall.project_id.in_(pids))
                                           .group_by(ToolCall.status))).all())
        usage = (await s.execute(select(func.count().label("n"), func.sum(ModelUsage.cost_usd).label("cost"),
                                        func.sum(ModelUsage.input_tokens).label("tin"), func.sum(ModelUsage.output_tokens).label("tout"))
                                 .where(ModelUsage.project_id.in_(pids)))).one()
        failed_calls = (await s.execute(select(func.count()).where(ModelUsage.project_id.in_(pids), ModelUsage.success.is_(False)))).scalar()
        fallbacks = (await s.execute(select(func.count()).where(ModelUsage.project_id.in_(pids), ModelUsage.fallback_from.is_not(None)))).scalar()
        recovered = (await s.execute(select(func.count()).where(EventRecord.project_id.in_(pids), EventRecord.type == "TASK_RECOVERED"))).scalar()

    durations = [finished[p] - started[p] for p in finished]
    task_lat = [(t.completed_at - t.started_at).total_seconds() for t in tasks if t.started_at and t.completed_at]
    completed = sum(t.status == "COMPLETED" for t in tasks)
    return {
        "config": {k: v for k, v in vars(args).items() if k not in ("out", "progress")},
        "wall_clock_s": round(wall, 2),
        "projects": {"total": len(pids), "finished": len(finished), "by_status": statuses,
                     "throughput_per_min": round(len(finished) / wall * 60, 2)},
        "project_latency_s": {"p50": pct(durations, 50), "p95": pct(durations, 95), "p99": pct(durations, 99),
                              "mean": round(statistics.fmean(durations), 3) if durations else 0},
        "tasks": {"total": len(tasks), "completed": completed, "retried": sum(t.retries > 0 for t in tasks),
                  "revised": sum(t.revision > 0 for t in tasks), "throughput_per_s": round(completed / wall, 2),
                  "latency_s": {"p50": pct(task_lat, 50), "p95": pct(task_lat, 95)}},
        "queue_depth": {"max": max(depth_samples, default=0), "mean": round(statistics.fmean(depth_samples), 2) if depth_samples else 0},
        "model_calls": {"total": usage.n, "failed": failed_calls, "fallbacks": fallbacks,
                        "tokens": int((usage.tin or 0) + (usage.tout or 0)), "cost_usd": round(float(usage.cost or 0), 4)},
        "tool_calls": tool_stats,
        "approvals_decided": decided,
        "recovered_tasks": recovered,
        "jobs_processed": pool.processed,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects", type=int, default=30)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--mix", default="support:3,b2b_sales:1", help="weighted templates, e.g. support:3,b2b_sales:1")
    ap.add_argument("--chaos", type=float, default=0.0, help="base fault-injection rate 0..1")
    ap.add_argument("--arrival-ms", type=int, default=0, help="delay between project submissions")
    ap.add_argument("--approval-think-s", type=float, default=0.5, help="simulated reviewer latency")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--out", default="")
    ap.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()
    report = asyncio.run(run(args))
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    if report["projects"]["finished"] < report["projects"]["total"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
