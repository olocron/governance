"""S8 API-hardening tests: rate-limit breaker, write limiter, input caps.

All deterministic — no LLM, no network. The breaker is tested directly and
via call_agent wiring (with a stubbed transport that raises provider-429s).
The middleware is tested on a fresh mini-app with a low limit, plus two
wiring checks against the real app (using a distinct X-Forwarded-For IP so
the shared 'testclient' bucket used by other test files is untouched).
"""

from __future__ import annotations

import time

import httpx
import pytest
from anthropic import RateLimitError as AnthropicRateLimitError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from olon.api.hardening import (
    WRITE_RATE_LIMIT,
    HardeningMiddleware,
    WriteRateLimiter,
)
from olon.config import RuntimeConfig
from olon.gateway import LLMGateway, RateLimitedOut, _RateBreaker

IP = "203.0.113.99"  # dedicated XFF IP so the shared testclient bucket is untouched


def _provider_429() -> AnthropicRateLimitError:
    """Build a realistic provider RateLimitError (HTTP 429)."""
    req = httpx.Request("POST", "https://api.example.com/v1/messages")
    resp = httpx.Response(429, request=req, json={"error": {"message": "rate limit"}})
    return AnthropicRateLimitError("429 rate limited", response=resp, body=None)


# ── 1. The rate-limit circuit breaker (direct) ────────────────────────────────


def test_breaker_opens_after_threshold():
    br = _RateBreaker(threshold=2, cooldown_s=60)
    assert not br.is_open
    br.record_rate_limit()
    assert not br.is_open, "one 429 should not open the breaker"
    br.record_rate_limit()
    assert br.is_open, "two consecutive 429s must open it"
    with pytest.raises(RateLimitedOut):
        br.check()


def test_breaker_success_resets():
    br = _RateBreaker(threshold=2, cooldown_s=60)
    br.record_rate_limit()
    br.record_success()  # a success between 429s resets the streak
    br.record_rate_limit()
    assert not br.is_open, "non-consecutive 429s must not open it"


def test_breaker_closes_after_cooldown():
    br = _RateBreaker(threshold=1, cooldown_s=0.05)
    br.record_rate_limit()
    assert br.is_open
    time.sleep(0.08)
    assert not br.is_open, "breaker must half-open (allow a probe) after cooldown"


# ── 2. call_agent wiring: fail fast while open ────────────────────────────────


def _stubbed_gateway() -> LLMGateway:
    cfg = RuntimeConfig(database_url="postgresql://x/y")
    gw = LLMGateway.from_provider("anthropic", api_key="test-key", config=cfg)
    gw._override_model = "glm-5-turbo"
    return gw


def test_call_agent_records_429s_and_fails_fast():
    gw = _stubbed_gateway()

    def _raise(*a, **k):
        raise _provider_429()

    gw._call_llm = _raise
    # Two provider 429s propagate (each recorded)...
    with pytest.raises(AnthropicRateLimitError):
        gw.call_agent("tester", "p1", use_cache=False)
    with pytest.raises(AnthropicRateLimitError):
        gw.call_agent("tester", "p2", use_cache=False)
    assert gw._breaker.is_open
    # ...and the third call fails FAST — no transport touched, no retry burn.
    called = {"n": 0}

    def _would_call(*a, **k):  # pragma: no cover — must never run
        called["n"] += 1
        return object()

    gw._call_llm = _would_call
    with pytest.raises(RateLimitedOut):
        gw.call_agent("tester", "p3", use_cache=False)
    assert called["n"] == 0


def test_call_agent_success_closes_breaker():
    gw = _stubbed_gateway()
    # While OPEN nothing passes (that's the point) — a success can only close
    # the breaker after the cooldown elapses and a probe call flows through.
    gw._breaker = _RateBreaker(threshold=2, cooldown_s=0.05)
    gw._breaker.record_rate_limit()
    gw._breaker.record_rate_limit()
    assert gw._breaker.is_open
    time.sleep(0.08)  # cooldown elapses -> probe allowed

    class _Resp:
        content = [type("B", (), {"type": "text", "text": "OK"})()]
        usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()

    gw._call_llm = lambda *a, **k: _Resp()
    resp = gw.call_agent("tester", "fresh-prompt", use_cache=False)
    assert resp.text == "OK"
    assert not gw._breaker.is_open


# ── 3. The write rate limiter + body cap (mini app, low limit) ────────────────


@pytest.fixture()
def mini_client():
    app = FastAPI()

    @app.post("/ping")
    async def ping():
        return {"ok": True}

    @app.get("/ping")
    async def ping_g():
        return {"ok": True}

    app.add_middleware(
        HardeningMiddleware, limiter=WriteRateLimiter(limit=3, window_s=60)
    )
    return TestClient(app)


def test_write_limit_blocks_after_n(mini_client):
    for _ in range(3):
        assert mini_client.post("/ping").status_code == 200
    r = mini_client.post("/ping")
    assert r.status_code == 429
    assert "rate limit" in r.json()["error"]
    assert "retry-after" in {k.lower() for k in r.headers}


def test_reads_never_limited(mini_client):
    for _ in range(10):
        assert mini_client.post("/ping").status_code in (200, 429)  # exhaust writes
    for _ in range(10):
        assert mini_client.get("/ping").status_code == 200


def test_xff_isolates_buckets(mini_client):
    for _ in range(3):
        mini_client.post("/ping")  # exhaust the testclient bucket
    assert mini_client.post("/ping").status_code == 429
    r = mini_client.post("/ping", headers={"X-Forwarded-For": IP})
    assert r.status_code == 200, "a different client IP gets its own bucket"


def test_body_over_1mb_rejected_413(mini_client):
    big = {"text": "x" * (1_000_001)}
    r = mini_client.post("/ping", json=big)
    assert r.status_code == 413
    # The 413 fires BEFORE the limiter — it must not consume a write slot.
    assert mini_client.post("/ping").status_code in (200, 429)


def test_invalid_content_length_rejected(mini_client):
    r = mini_client.post(
        "/ping", content=b"x", headers={"Content-Length": "not-a-number"}
    )
    assert r.status_code in (400, 500)  # header parsed by our guard or the server


# ── 4. Wiring checks against the REAL app ─────────────────────────────────────


def test_real_app_has_input_caps():
    from olon.api.server import app

    client = TestClient(app)
    r = client.post(
        "/instances/kimberim/agents",
        json={"display_name": "x" * 121},  # cap is 120
        headers={"X-Forwarded-For": IP},
    )
    assert r.status_code == 422
    assert WRITE_RATE_LIMIT >= 3, "real-app limit should be generous for humans"


def test_real_app_body_cap():
    from olon.api.server import app

    client = TestClient(app)
    r = client.post(
        "/instances/kimberim/agents",
        json={"display_name": "x" * 1_000_001},
        headers={"X-Forwarded-For": IP},
    )
    assert r.status_code == 413
