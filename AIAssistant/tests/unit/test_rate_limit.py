"""Unit tests for the HTTP admission governor (ADR 0024)."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from ai_assistant.adapters.http import rate_limit as rate_limit_module
from ai_assistant.adapters.http.rate_limit import PeerRateLimiter, RateLimitMiddleware


class _Recorder:
    """Minimal ASGI app recording how often it was reached."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _http_scope(path: str = "/v1/agent/execute", client=("10.0.0.7", 1234)):
    return {"type": "http", "path": path, "headers": [], "client": client}


def _collect(send, messages):
    async def collector(message):
        messages.append(message)
    return collector


class PeerRateLimiterTests(unittest.TestCase):
    def test_a_zero_budget_disables_the_limiter(self) -> None:
        limiter = PeerRateLimiter(0)
        self.assertFalse(limiter.enabled)
        for _ in range(10):
            self.assertIsNone(limiter.allow("peer"))

    def test_the_budget_is_per_peer_and_resets_with_the_window(self) -> None:
        limiter = PeerRateLimiter(2, window_seconds=60.0)
        self.assertIsNone(limiter.allow("a", now=0.0))
        self.assertIsNone(limiter.allow("a", now=1.0))
        retry_after = limiter.allow("a", now=2.0)
        self.assertIsNotNone(retry_after)
        self.assertAlmostEqual(retry_after or 0.0, 58.0)
        # A different peer keeps its own window.
        self.assertIsNone(limiter.allow("b", now=2.0))
        # The next window admits again.
        self.assertIsNone(limiter.allow("a", now=60.0))

    def test_expired_counters_are_pruned_past_the_threshold(self) -> None:
        limiter = PeerRateLimiter(5, window_seconds=60.0)
        with mock.patch.object(rate_limit_module, "_PRUNE_THRESHOLD", 2):
            for index in range(3):
                limiter.allow(f"peer-{index}", now=float(index))
            self.assertEqual(len(limiter._counters), 3)
            # Nothing is admitted yet; the next call sees a full map, prunes
            # the expired entries and then records the fresh peer.
            limiter.allow("peer-new", now=1000.0)
            self.assertEqual(list(limiter._counters), ["peer-new"])


class RateLimitMiddlewareTests(unittest.TestCase):
    def test_non_http_scopes_pass_through(self) -> None:
        app = _Recorder()
        middleware = RateLimitMiddleware(app, PeerRateLimiter(1))
        _run(middleware({"type": "lifespan"}, None, _collect(None, [])))
        self.assertEqual(app.calls, 1)

    def test_a_disabled_limiter_never_refuses(self) -> None:
        app = _Recorder()
        middleware = RateLimitMiddleware(app, PeerRateLimiter(0))
        messages: list[dict] = []
        _run(middleware(_http_scope(), None, _collect(None, messages)))
        self.assertEqual(app.calls, 1)
        self.assertEqual(messages[0]["status"], 200)

    def test_exhausted_budget_answers_429_with_retry_after(self) -> None:
        app = _Recorder()
        middleware = RateLimitMiddleware(app, PeerRateLimiter(1))
        _run(middleware(_http_scope(), None, _collect(None, [])))
        messages: list[dict] = []
        _run(middleware(_http_scope(), None, _collect(None, messages)))
        self.assertEqual(app.calls, 1, "the refusal must not reach the app")
        self.assertEqual(messages[0]["status"], 429)
        headers = dict(messages[0]["headers"])
        self.assertEqual(headers[b"content-type"], b"application/json")
        self.assertEqual(headers[b"retry-after"], b"60")
        self.assertEqual(json.loads(messages[1]["body"]), {"detail": "Rate limit exceeded"})

    def test_probes_are_exempt_from_the_budget(self) -> None:
        app = _Recorder()
        middleware = RateLimitMiddleware(app, PeerRateLimiter(1))
        for _ in range(3):
            _run(middleware(_http_scope(path="/readyz"), None, _collect(None, [])))
        self.assertEqual(app.calls, 3)

    def test_a_missing_client_uses_the_unknown_bucket(self) -> None:
        app = _Recorder()
        middleware = RateLimitMiddleware(app, PeerRateLimiter(1))
        _run(middleware(_http_scope(client=None), None, _collect(None, [])))
        messages: list[dict] = []
        _run(middleware(_http_scope(client=None), None, _collect(None, messages)))
        self.assertEqual(app.calls, 1)
        self.assertEqual(messages[0]["status"], 429)

    def test_a_refusal_is_counted_on_the_metrics_facade(self) -> None:
        class _Counter:
            def __init__(self) -> None:
                self.observed: list[str] = []

            def labels(self, surface: str):
                self.observed.append(surface)
                return self

            def inc(self) -> None:
                pass

        counter = _Counter()
        app = _Recorder()
        middleware = RateLimitMiddleware(app, PeerRateLimiter(1))
        with mock.patch.object(
            rate_limit_module, "record_throttle",
            side_effect=lambda surface: counter.labels(surface).inc(),
        ) as recorded:
            _run(middleware(_http_scope(), None, _collect(None, [])))
            _run(middleware(_http_scope(), None, _collect(None, [])))
        recorded.assert_called_once_with("http")
        self.assertEqual(counter.observed, ["http"])


class GovernorLoadTests(unittest.TestCase):
    """A burst through a real ASGI stack: admitted up to the budget, then 429.

    This is the load/chaos smoke check for the governor (ADR 0024): the surface
    must throttle with a retryable status and never degrade into 5xx.
    """

    def test_a_burst_is_admitted_then_throttled_without_server_errors(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/ping")
        def ping() -> dict[str, bool]:
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, limiter=PeerRateLimiter(5))
        client = TestClient(app)
        statuses = [client.get("/ping").status_code for _ in range(8)]
        self.assertEqual(statuses.count(200), 5)
        self.assertEqual(statuses.count(429), 3)
        self.assertNotIn(500, statuses)
        last = client.get("/ping")
        self.assertEqual(last.status_code, 429)
        self.assertIn("retry-after", {name.lower() for name in last.headers})


if __name__ == "__main__":
    unittest.main()
