"""Agent worker pool. Runs embedded in the API (dev) or standalone: `python -m app.worker`."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.events import EventType, emit
from app.core.telemetry import ACTIVE_WORKERS, RECOVERIES, TASK_DURATION, span
from app.models import Project, ProjectStatus, Task
from app.services import orchestrator
from app.services.agents.runtime import AgentRuntime, RunResult
from app.services.planning.planner import PlanningFailed, create_plan
from app.services.queue import Job, JobQueue, get_queue

log = logging.getLogger("agentos.worker")


class WorkerPool:
    def __init__(self, concurrency: int, name: str | None = None, queue: JobQueue | None = None,
                 runtime: AgentRuntime | None = None, recovery_interval: float | None = None):
        self.concurrency = concurrency
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self._queue = queue
        self.runtime = runtime or AgentRuntime()
        self.recovery_interval = recovery_interval if recovery_interval is not None else get_settings().recovery_interval_seconds
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()
        self.processed = 0
        self.busy = 0

    @property
    def queue(self) -> JobQueue:
        return self._queue or get_queue()

    async def start(self) -> None:
        self._stopping.clear()
        stats = await orchestrator.recover(stale_queued_after_s=0)
        if any(stats.values()):
            RECOVERIES.labels("startup").inc()
            log.info("startup recovery: %s", stats)
        for i in range(self.concurrency):
            self._tasks.append(asyncio.create_task(self._loop(f"{self.name}:{i}")))
        self._tasks.append(asyncio.create_task(self._recovery_loop()))

    async def stop(self) -> None:
        self._stopping.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _recovery_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.sleep(self.recovery_interval)
                stats = await orchestrator.recover()
                for k, v in stats.items():
                    if v:
                        RECOVERIES.labels(k).inc(v)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("recovery sweep failed")

    async def _loop(self, worker_id: str) -> None:
        while not self._stopping.is_set():
            try:
                job = await self.queue.get(worker_id, timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("queue read failed")
                await asyncio.sleep(1)
                continue
            if job is None:
                continue
            self.busy += 1
            ACTIVE_WORKERS.inc()
            try:
                await self.handle(job, worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("job %s failed", job.id)
            finally:
                self.busy -= 1
                self.processed += 1
                ACTIVE_WORKERS.dec()
                await self.queue.ack(worker_id, job)

    async def handle(self, job: Job, worker_id: str) -> None:
        if job.type == "plan_project":
            await self._plan(job.id)
        elif job.type == "run_task":
            await self._run_task(job.id, worker_id)

    async def _plan(self, project_id: str) -> None:
        async with session_scope() as s:
            p = await s.get(Project, project_id)
            if p is None or p.status != ProjectStatus.PLANNING:
                return
        with span("plan.create", project=project_id):
            try:
                await create_plan(project_id)
            except PlanningFailed as exc:
                async with session_scope() as s:
                    p = await s.get(Project, project_id)
                    p.status = ProjectStatus.FAILED
                    emit(s, p.org_id, EventType.PROJECT_FAILED, project_id=p.id, agent_key="supervisor", message=str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - provider outage etc.: leave PLANNING for recovery to retry
                log.warning("planning failed for %s: %s", project_id, exc)
                async with session_scope() as s:
                    p = await s.get(Project, project_id)
                    emit(s, p.org_id, EventType.AGENT_STEP, project_id=p.id, agent_key="supervisor",
                         message=f"Planning attempt failed ({exc}); will retry", payload={"phase": "PLAN"})
                return
        await orchestrator.schedule(project_id)

    async def _run_task(self, task_id: str, worker_id: str) -> None:
        if not await orchestrator.claim(task_id, worker_id):
            return
        t0 = time.perf_counter()
        async with session_scope() as s:
            agent_key = (await s.get(Task, task_id)).agent_key
        with span("task.run", task=task_id, agent=agent_key, worker=worker_id):
            try:
                result = await self.runtime.run(task_id, worker_id)
            except asyncio.CancelledError:
                raise  # lease will expire and recovery resumes from checkpoint
            except Exception as exc:  # noqa: BLE001
                log.exception("runtime crashed on task %s", task_id)
                result = RunResult("failed_transient", error=f"runtime error: {type(exc).__name__}: {exc}")
        TASK_DURATION.labels(agent_key).observe(time.perf_counter() - t0)
        await orchestrator.handle_result(task_id, result)


_pool: WorkerPool | None = None


def get_pool() -> WorkerPool | None:
    return _pool


def set_pool(p: WorkerPool | None) -> None:
    global _pool
    _pool = p
