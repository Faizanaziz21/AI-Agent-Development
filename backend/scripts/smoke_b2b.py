"""Run the flagship B2B project end-to-end in-process, auto-approving approvals. Usage: python scripts/smoke_b2b.py"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AGENTOS_DATABASE_URL", "sqlite+aiosqlite:///./data/smoke.db")
os.environ.setdefault("AGENTOS_LOCAL_MODEL_LATENCY_MS", "0")

from sqlalchemy import select  # noqa: E402

from app.core.db import create_all, init_engine, session_scope  # noqa: E402
from app.core.security import Principal  # noqa: E402
from app.models import Approval, Organization, Project, Task, User  # noqa: E402
from app.seed.seed import seed_all  # noqa: E402
from app.services import approvals, orchestrator  # noqa: E402
from app.services.projects import TEMPLATES, create_project  # noqa: E402
from app.services.worker import WorkerPool  # noqa: E402


async def main(template: str = "b2b_sales") -> None:
    if os.path.exists("data/smoke.db"):
        os.remove("data/smoke.db")
    init_engine()
    await create_all()
    async with session_scope() as s:
        await seed_all(s)
    async with session_scope() as s:
        org = (await s.execute(select(Organization).where(Organization.slug == "sentinel"))).scalar_one()
        user = (await s.execute(select(User).where(User.email == "admin@sentinel.example"))).scalar_one()
        if template == "support":
            from app.services.projects import DEMO_TICKETS, create_support_ticket

            pids = []
            for tk in DEMO_TICKETS:
                p, _ = await create_support_ticket(s, org_id=org.id, user_id=user.id, **tk)
                pids.append(p.id)
        else:
            t = TEMPLATES[template]
            p = await create_project(s, org_id=org.id, user_id=user.id, name=t["name"], objective=t["objective"],
                                     parameters=t["parameters"], template=template, budget_usd=t["budget_usd"])
            pids = [p.id]
        principal = Principal(user.id, org.id, "owner", user.email, user.name)
    pool = WorkerPool(6, recovery_interval=2)
    await pool.start()
    for pid in pids:
        await orchestrator.start_project(pid)
    t0 = time.time()
    while time.time() - t0 < 300:
        await asyncio.sleep(1)
        async with session_scope() as s:
            ps = [await s.get(Project, pid) for pid in pids]
            pend = list((await s.execute(select(Approval).where(Approval.project_id.in_(pids), Approval.status == "PENDING"))).scalars())
            for a in pend:
                print("APPROVING:", a.title, a.action_type, a.reason[:120])
                await approvals.decide(s, a, principal, "approve")
            ids = [a.id for a in pend]
            done = all(p.status in ("COMPLETED", "FAILED", "CANCELLED") for p in ps)
        for i in ids:
            await orchestrator.apply_approval(i)
        if done:
            break
    await pool.stop()
    for pid in pids:
      async with session_scope() as s:
        p = await s.get(Project, pid)
        print("PROJECT", p.name, p.status, f"${p.spent_usd:.4f}", p.tokens_used, f"{time.time() - t0:.1f}s")
        for t in (await s.execute(select(Task).where(Task.project_id == pid).order_by(Task.created_at))).scalars():
            print(f"  {t.status:16} {t.key:28} {t.agent_key:15} rev={t.revision} att={t.attempt} q={t.quality_score} | {t.output_summary[:90]} {t.error or ''}")
        if p.deliverable:
            print("DELIVERABLE:", p.deliverable.get("headline") or p.deliverable.get("summary"))
            for o in p.deliverable.get("top_opportunities", [])[:25]:
                print("   ", o["rank"], o["company"], o["industry"], o["employees"], o["rank_score"])


if __name__ == "__main__":
    asyncio.run(main(*(sys.argv[1:2] or ["b2b_sales"])))
