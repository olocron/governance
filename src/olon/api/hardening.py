"""API hardening middleware (S8): per-IP write rate limiting + body size cap.

The engage API is intentionally unauthenticated in the MVP (agent_id is the
handle, holacracy-style open participation), which makes write endpoints
abusable by anyone with curl. This module adds proportionate, in-process
guards — the same trust model as the FeedBroker (single-node MVP):

  1. Per-IP rate limit on WRITE methods (POST/PUT/PATCH/DELETE):
     default 20 writes / 60s per client IP. Reads stay unlimited.
  2. Request body cap: Content-Length over 1 MB is rejected with 413 before
     the body is read.

Client IP resolution honours X-Forwarded-For because in production Caddy
reverse-proxies the API (uvicorn would otherwise see only Caddy's container
IP and rate-limit the whole world as one client).

Both limits return the API's {"error": ...} JSON shape, and the middleware
sits INSIDE CORS so browser callers can read the error bodies.
"""

from __future__ import annotations

import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ── Tunables ──────────────────────────────────────────────────────────────────
WRITE_RATE_LIMIT = 20          # writes per window per client IP
WRITE_RATE_WINDOW_S = 60.0
MAX_BODY_BYTES = 1_000_000     # 1 MB — JSON payloads here are < 10 KB
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _SlidingWindow:
    """A fixed-size sliding window of request timestamps (per IP)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: deque[float] = deque()

    def allow(self, now: float) -> bool:
        # Purge hits outside the window.
        while self._hits and now - self._hits[0] >= self.window_s:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True

    def retry_after_s(self, now: float) -> float:
        """Seconds until the oldest hit ages out of the window."""
        if not self._hits:
            return 0.0
        return max(0.0, self.window_s - (now - self._hits[0]))


class WriteRateLimiter:
    """In-memory per-IP write limiter. Single-node by design (MVP)."""

    def __init__(self, limit: int = WRITE_RATE_LIMIT,
                 window_s: float = WRITE_RATE_WINDOW_S) -> None:
        self.limit = limit
        self.window_s = window_s
        self._windows: dict[str, _SlidingWindow] = {}
        self._last_sweep = time.monotonic()

    def _sweep(self, now: float) -> None:
        """Drop idle windows so the dict can't grow unboundedly."""
        if now - self._last_sweep < self.window_s:
            return
        self._windows = {
            ip: w for ip, w in self._windows.items()
            if w._hits and now - w._hits[-1] < self.window_s
        }
        self._last_sweep = now

    def check(self, ip: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_s)."""
        now = time.monotonic()
        self._sweep(now)
        w = self._windows.get(ip)
        if w is None:
            w = self._windows[ip] = _SlidingWindow(self.limit, self.window_s)
        return w.allow(now), w.retry_after_s(now)


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Prefers X-Forwarded-For (first entry = the
    original client) because production traffic arrives via the Caddy proxy;
    falls back to the socket peer for direct/dev access."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


class HardeningMiddleware(BaseHTTPMiddleware):
    """Per-IP write rate limit + request body cap. Reads are untouched."""

    def __init__(self, app, *, limiter: WriteRateLimiter | None = None,
                 max_body_bytes: int = MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self.limiter = limiter or WriteRateLimiter()
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next):
        # Body size cap (cheap: header check before the body is read).
        if request.method in _WRITE_METHODS:
            length = request.headers.get("content-length")
            if length:
                try:
                    if int(length) > self.max_body_bytes:
                        return JSONResponse(
                            {"error": "request body too large"},
                            status_code=413,
                        )
                except ValueError:
                    return JSONResponse(
                        {"error": "invalid content-length"}, status_code=400
                    )

            # Per-IP write rate limit.
            ip = _client_ip(request)
            allowed, retry_after = self.limiter.check(ip)
            if not allowed:
                return JSONResponse(
                    {"error": "rate limit exceeded; slow down"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(retry_after) + 1))},
                )

        return await call_next(request)


__all__ = ["HardeningMiddleware", "WriteRateLimiter"]
