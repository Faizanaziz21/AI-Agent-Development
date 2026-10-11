"""Worker queue.

The database is the source of truth for task state; the queue only carries "work is ready"
notifications. Delivery is at-least-once: workers claim a task with an atomic conditional UPDATE
(a lease), so duplicate or stale jobs are harmless, and the recovery loop re-enqueues anything a
crashed worker or a lost message left behind.

Backends:
* InMemoryQueue — single process (dev, tests, embedded workers).
* RedisQueue    — distributed workers. Ready list + delayed ZSET + per-worker processing lists
                  (BLMOVE) so in-flight jobs survive worker crashes and are requeued by recovery.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.telemetry import QUEUE_DEPTH


@dataclass(order=True)
class Job:
    priority: int
    seq: int
    type: str = field(compare=False)
    id: str = field(compare=False)
    org_id: str = field(compare=False, default="")
    payload: dict[str, Any] = field(compare=False, default_factory=dict)

    def dumps(self) -> str:
        return json.dumps({"priority": self.priority, "seq": self.seq, "type": self.type, "id": self.id,
                           "org_id": self.org_id, "payload": self.payload})

    @classmethod
    def loads(cls, s: str) -> Job:
        return cls(**json.loads(s))


_seq = itertools.count()


def make_job(type: str, id: str, org_id: str = "", priority: int = 5, payload: dict | None = None) -> Job:
    # lower number = served first; task priority 10 (highest) maps to 0
    return Job(10 - priority, next(_seq), type, id, org_id, payload or {})


class JobQueue(ABC):
    @abstractmethod
    async def put(self, job: Job, delay_s: float = 0) -> None: ...

    @abstractmethod
    async def get(self, worker_id: str, timeout: float = 1.0) -> Job | None: ...

    async def ack(self, worker_id: str, job: Job) -> None:  # noqa: B027
        pass

    @abstractmethod
    async def depth(self) -> int: ...


class InMemoryQueue(JobQueue):
    def __init__(self) -> None:
        self._heap: list[Job] = []
        self._delayed: list[tuple[float, int, Job]] = []
        self._cond = asyncio.Condition()

    async def put(self, job: Job, delay_s: float = 0) -> None:
        async with self._cond:
            if delay_s > 0:
                heapq.heappush(self._delayed, (time.monotonic() + delay_s, job.seq, job))
            else:
                heapq.heappush(self._heap, job)
            QUEUE_DEPTH.set(len(self._heap))
            self._cond.notify()

    def _promote(self) -> None:
        now = time.monotonic()
        while self._delayed and self._delayed[0][0] <= now:
            heapq.heappush(self._heap, heapq.heappop(self._delayed)[2])

    async def get(self, worker_id: str, timeout: float = 1.0) -> Job | None:
        deadline = time.monotonic() + timeout
        async with self._cond:
            while True:
                self._promote()
                if self._heap:
                    job = heapq.heappop(self._heap)
                    QUEUE_DEPTH.set(len(self._heap))
                    return job
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                wait = min(remaining, (self._delayed[0][0] - time.monotonic()) if self._delayed else remaining)
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=max(wait, 0.01))
                except TimeoutError:
                    pass

    async def depth(self) -> int:
        return len(self._heap) + len(self._delayed)


class RedisQueue(JobQueue):
    READY = "agentos:jobs:ready"
    DELAYED = "agentos:jobs:delayed"

    def __init__(self, client) -> None:
        self.r = client

    def _processing(self, worker_id: str) -> str:
        return f"agentos:jobs:processing:{worker_id}"

    async def put(self, job: Job, delay_s: float = 0) -> None:
        if delay_s > 0:
            await self.r.zadd(self.DELAYED, {job.dumps(): time.time() + delay_s})
        else:
            await self.r.lpush(self.READY, job.dumps())

    async def _promote(self) -> None:
        now = time.time()
        due = await self.r.zrangebyscore(self.DELAYED, 0, now, start=0, num=100)
        for item in due:
            if await self.r.zrem(self.DELAYED, item):
                await self.r.lpush(self.READY, item)

    async def get(self, worker_id: str, timeout: float = 1.0) -> Job | None:
        await self._promote()
        item = await self.r.blmove(self.READY, self._processing(worker_id), timeout, "RIGHT", "LEFT")
        if item is None:
            return None
        return Job.loads(item if isinstance(item, str) else item.decode())

    async def ack(self, worker_id: str, job: Job) -> None:
        await self.r.lrem(self._processing(worker_id), 1, job.dumps())

    async def requeue_orphans(self, live_workers: set[str]) -> int:
        n = 0
        async for key in self.r.scan_iter("agentos:jobs:processing:*"):
            k = key if isinstance(key, str) else key.decode()
            if k.rsplit(":", 1)[-1] in live_workers:
                continue
            while await self.r.lmove(k, self.READY, "RIGHT", "LEFT"):
                n += 1
        return n

    async def depth(self) -> int:
        return int(await self.r.llen(self.READY)) + int(await self.r.zcard(self.DELAYED))


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    global _queue
    if _queue is None:
        from app.core.config import get_settings

        url = get_settings().redis_url
        if url:
            import redis.asyncio as redis

            _queue = RedisQueue(redis.from_url(url, decode_responses=True))
        else:
            _queue = InMemoryQueue()
    return _queue


def set_queue(q: JobQueue | None) -> None:
    global _queue
    _queue = q
