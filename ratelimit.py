"""
Vouch — rate limiting.

In-memory sliding windows per client IP. No Redis, no extra dependency:
a single Vouch server is one process, so a dict is enough. Limits are
generous to humans and tight to floods; exceeding one returns 429.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# path prefix -> (max requests, window seconds)
LIMITS = [
    ("/api/auth/", 20, 60),
    ("/api/extract/", 60, 60),
    ("/api/scan/", 60, 60),
    ("/api/ask", 60, 60),
    ("/api/agent/", 60, 60),
    ("/api/explain", 60, 60),
    ("/api/", 300, 60),
]


class Limiter:
    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], list[float]] = {}

    def allow(self, ip: str, path: str,
              now: float | None = None) -> bool:
        now = time.time() if now is None else now
        limit, window = 300, 60
        for prefix, count, span in LIMITS:
            if path.startswith(prefix):
                limit, window = count, span
                break
        key = (ip or "?", path.split("?")[0].rsplit("/", 1)[0] + "/")
        seen = [t for t in self.hits.get(key, []) if now - t < window]
        if len(seen) >= limit:
            self.hits[key] = seen
            return False
        seen.append(now)
        self.hits[key] = seen
        # Forget idle clients so the dict cannot grow forever.
        if len(self.hits) > 5000:
            self.hits = {k: v for k, v in self.hits.items()
                         if v and now - v[-1] < 600}
        return True


LIMITER = Limiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        ip = request.client.host if request.client else "?"
        if not LIMITER.allow(ip, request.url.path):
            return JSONResponse(
                {"detail": "Too many requests. Slow down and retry."},
                status_code=429,
            )
        return await call_next(request)
