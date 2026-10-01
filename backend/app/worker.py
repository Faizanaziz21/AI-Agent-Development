"""Standalone worker process entrypoint (horizontally scalable)."""

from __future__ import annotations

import asyncio
import os
import signal

from prometheus_client import start_http_server

from app.core.config import get_settings
from app.core.db import init_engine
from app.core.events import bus
from app.core.telemetry import setup_logging, setup_tracing
from app.services.worker import WorkerPool


async def main() -> None:
    setup_logging()
    setup_tracing("agentos-worker")
    init_engine()
    bus.bind_loop(asyncio.get_running_loop())
    s = get_settings()
    if s.redis_url:
        import redis.asyncio as redis

        bus.set_redis(redis.from_url(s.redis_url, decode_responses=True))
    start_http_server(int(os.environ.get("AGENTOS_WORKER_METRICS_PORT", "9100")))
    pool = WorkerPool(int(os.environ.get("AGENTOS_WORKER_CONCURRENCY", "8")))
    await pool.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
