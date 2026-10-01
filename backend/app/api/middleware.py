from __future__ import annotations

import time
import uuid
from collections import OrderedDict

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.security import decode_access_token
from app.core.telemetry import HTTP_LATENCY, HTTP_REQUESTS

EXEMPT_PREFIXES = ("/healthz", "/readyz", "/metrics")


class RateLimiter:
    """Fixed-window limiter keyed by user (or client IP when unauthenticated).

    Uses Redis INCR/EXPIRE when configured so limits hold across API replicas; otherwise an
    in-process LRU of windows, which is exact for a single replica."""

    def __init__(self, per_minute: int, redis=None, max_keys: int = 50_000):
        self.per_minute = per_minute
        self.redis = redis
        self.max_keys = max_keys
        self._windows: OrderedDict[str, tuple[int, int]] = OrderedDict()

    async def hit(self, key: str) -> tuple[bool, int, int]:
        window = int(time.time() // 60)
        reset = (window + 1) * 60 - int(time.time())
        if self.redis is not None:
            rk = f"agentos:rl:{key}:{window}"
            count = await self.redis.incr(rk)
            if count == 1:
                await self.redis.expire(rk, 65)
        else:
            w, count = self._windows.get(key, (window, 0))
            count = count + 1 if w == window else 1
            self._windows[key] = (window, count)
            self._windows.move_to_end(key)
            while len(self._windows) > self.max_keys:
                self._windows.popitem(last=False)
        return count <= self.per_minute, max(0, self.per_minute - count), reset


def _limit_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            return "u:" + str(decode_access_token(auth[7:]).get("sub"))
        except jwt.PyJWTError:
            pass
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
    return "ip:" + ip


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: RateLimiter):
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(EXEMPT_PREFIXES) or request.method == "OPTIONS":
            return await call_next(request)
        ok, remaining, reset = await self.limiter.hit(_limit_key(request))
        if not ok:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                                headers={"Retry-After": str(reset), "X-RateLimit-Remaining": "0"})
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limiter.per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - t0)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response
