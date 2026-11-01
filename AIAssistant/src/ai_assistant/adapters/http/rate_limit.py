"""Admission control for the HTTP surface (ADR 0024).

One fixed-window budget per peer address. Fixed windows (rather than a token
bucket) are the shape an ops runbook can state exactly — "N requests per
minute per caller" — and the refusal is the standard ``429`` +
``Retry-After`` pair. Liveness probes are exempt so a Docker healthcheck can
never starve under a tight budget.
"""
from __future__ import annotations

import math
import time
from typing import Any

from ...observability.metrics import record_throttle

#: Paths that must answer even when the budget is exhausted (container probe).
_EXEMPT_PATHS = frozenset({"/livez", "/readyz"})

#: Expired counters are pruned once the map grows past this size, so a
#: long-lived process cannot accumulate one entry per address seen forever.
_PRUNE_THRESHOLD = 256


class PeerRateLimiter:
    """Fixed-window request budget per peer address (``0`` = disabled)."""

    def __init__(self, limit_per_minute: int, window_seconds: float = 60.0) -> None:
        self._limit = limit_per_minute
        self._window = window_seconds
        #: peer -> (window_start_monotonic, requests_used_in_window)
        self._counters: dict[str, tuple[float, int]] = {}

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    def allow(self, peer: str, now: float | None = None) -> float | None:
        """Return ``None`` when admitted, else seconds until the window resets.

        ``now`` is injectable so the window behaviour is testable without
        sleeping through real minutes.
        """
        if not self.enabled:
            return None
        current = time.monotonic() if now is None else now
        self._prune(current)
        window_start, used = self._counters.get(peer, (current, 0))
        if current - window_start >= self._window:
            window_start, used = current, 0
        if used >= self._limit:
            return max(0.0, self._window - (current - window_start))
        self._counters[peer] = (window_start, used + 1)
        return None

    def _prune(self, current: float) -> None:
        if len(self._counters) <= _PRUNE_THRESHOLD:
            return
        self._counters = {
            peer: entry for peer, entry in self._counters.items()
            if current - entry[0] < self._window
        }


class RateLimitMiddleware:
    """Answer ``429`` + ``Retry-After`` when a peer's budget is spent."""

    def __init__(self, app: Any, limiter: PeerRateLimiter) -> None:
        self._app = app
        self._limiter = limiter

    async def _refuse(self, send: Any, retry_after: float) -> None:
        seconds = max(1, math.ceil(retry_after))
        await send({
            "type": "http.response.start", "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(seconds).encode("ascii")),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"Rate limit exceeded"}',
        })

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not self._limiter.enabled:
            await self._app(scope, receive, send)
            return
        if scope.get("path") in _EXEMPT_PATHS:
            await self._app(scope, receive, send)
            return
        client = scope.get("client")
        peer = client[0] if client else "unknown"
        retry_after = self._limiter.allow(peer)
        if retry_after is not None:
            record_throttle("http")
            await self._refuse(send, retry_after)
            return
        await self._app(scope, receive, send)


__all__ = [
    "PeerRateLimiter",
    "RateLimitMiddleware",
]
