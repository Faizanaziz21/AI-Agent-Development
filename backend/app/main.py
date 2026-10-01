"""AgentOS API gateway."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.middleware import ObservabilityMiddleware, RateLimiter, RateLimitMiddleware
from app.api.routes import agents, approvals, audit, auth, business, costs, knowledge, memory, orgs, projects, system, webhooks
from app.core.config import get_settings
from app.core.db import create_all, dispose, init_engine, session_scope
from app.core.events import bus
from app.core.telemetry import setup_logging, setup_tracing
from app.services.tools import builtin  # noqa: F401  (register tools)
from app.services.worker import WorkerPool, set_pool

log = logging.getLogger("agentos.api")


def _redis_client():
    settings = get_settings()
    if not settings.redis_url:
        return None
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.redis_url, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging()
    setup_tracing("agentos-api")
    if settings.env == "production" and settings.jwt_secret.startswith("dev-only"):
        raise RuntimeError("AGENTOS_JWT_SECRET must be set in production")
    init_engine()
    if settings.auto_create_schema:
        await create_all()
    if settings.seed_demo:
        from app.seed.seed import seed_all

        async with session_scope() as s:
            await seed_all(s)
    bus.bind_loop(asyncio.get_running_loop())
    redis = app.state.redis
    if redis is not None:
        bus.set_redis(redis)
    pool = None
    if settings.embedded_workers > 0:
        pool = WorkerPool(settings.embedded_workers, name=f"api-{os.getpid()}")
        await pool.start()
        set_pool(pool)
    app.state.ready = True
    log.info("AgentOS API ready (embedded workers=%s)", settings.embedded_workers)
    try:
        yield
    finally:
        app.state.ready = False
        if pool is not None:
            await pool.stop()
            set_pool(None)
        if redis is not None:
            await redis.aclose()
        await dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AgentOS API", version="0.1.0", lifespan=lifespan,
                  description="Enterprise multi-agent digital workforce platform.")
    app.state.ready = False
    app.state.redis = _redis_client()

    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(settings.rate_limit_per_minute, redis=app.state.redis))
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    api = APIRouter(prefix="/api/v1")
    for module in (auth, orgs, agents, knowledge, projects, approvals, memory, costs, audit, business, webhooks):
        api.include_router(module.router)
    app.include_router(api)
    app.include_router(system.router)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    if settings.static_dir and os.path.isdir(settings.static_dir):
        static_dir = os.path.abspath(settings.static_dir)
        app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = os.path.abspath(os.path.join(static_dir, path))
            if path and candidate.startswith(static_dir) and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(os.path.join(static_dir, "index.html"))

    return app


app = create_app()
