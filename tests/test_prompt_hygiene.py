"""H12 prompt-hygiene tests: the prompt-data invariant and its redaction
tripwires.

The invariant: nothing the platform wouldn't publish goes into any prompt —
because federation broadcasts every prompt verbatim to every self-hosted
endpoint agent (an exfiltration channel by construction).

Enforcement is REDACT-not-reject (a planted key-looking string in a tension
must not DoS a deliberation) at three choke points:
  1. sandbox()      — every untrusted-content interpolation
  2. LLMGateway.call_agent — the single platform transport choke point
  3. EndpointAdapter.respond — the federation transport

All tests are deterministic: the gateway's transport is monkeypatched, the
endpoint uses httpx MockTransport. No network, no LLM.
"""

from __future__ import annotations

import json

import httpx
from openai.types.chat import ChatCompletion

from olon.agents.endpoint import EndpointAdapter
from olon.config import RuntimeConfig
from olon.gateway import LLMGateway
from olon.schema import AgentRef, AgentRole
from olon.security import (
    PROMPT_DATA_INVARIANT,
    redact_secrets,
    sandbox,
    scan_secrets,
)

# ── The tripwire patterns ─────────────────────────────────────────────────────

_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"


def test_scan_secrets_matches_each_leak_class():
    cases = {
        _PRIVATE_KEY: "private-key",
        "my key is sk-abc123def456ghi789jkl012": "api-key",
        "AWS AKIAIOSFODNN7EXAMPLE leaked": "aws-access-key",
        "curl -H 'Authorization: Bearer abcdef1234567890abcdef'": "bearer-token",
        "postgres://olon:supersecret@db.local:5432/olon": "db-url-credentials",
        "token: ghp_" + "a" * 36: "github-token",
    }
    for text, expected in cases.items():
        matched = scan_secrets(text)
        assert expected in matched, f"{expected} should match {text[:40]!r}"


def test_scan_secrets_clean_on_ordinary_prompt_text():
    """No false positives on the text that actually flows through prompts:
    tensions, proposals, positions, governance prose."""
    ordinary = [
        "Grid export revenue crowds out the compute value-add",
        '{"position": "consent", "criterion": "not-safe-to-try"}',
        "The proposal caps compute at 30% of 1GW; reversible quarterly.",
        "Respond as JSON with keys: title, context, change.",
        "Bearer of cultural knowledge should be consulted",  # prose 'Bearer', short
        "skill-sharing across the collective",
    ]
    for text in ordinary:
        assert scan_secrets(text) == [], f"false positive on {text!r}"


def test_redact_secrets_removes_and_reports():
    text = f"connect via postgres://olon:supersecret@db.local/olon and {_PRIVATE_KEY}"
    redacted, matched = redact_secrets(text)
    assert set(matched) == {"db-url-credentials", "private-key"}
    assert "supersecret" not in redacted
    assert "MIIEow" not in redacted
    assert "[redacted:private-key]" in redacted
    assert "[redacted:db-url-credentials]" in redacted


def test_redact_secrets_passthrough_clean_text():
    out, matched = redact_secrets("an ordinary tension description")
    assert out == "an ordinary tension description"
    assert matched == []


# ── Choke point 1: sandbox() redacts ─────────────────────────────────────────


def test_sandbox_redacts_secret_shaped_untrusted_content():
    fenced = sandbox("tension description",
                     "api key sk-abc123def456ghi789jkl012 was leaked")
    assert "sk-abc123def456ghi789jkl012" not in fenced
    assert "[redacted:api-key]" in fenced
    # The fence structure itself is intact.
    assert fenced.startswith("[[[UNTRUSTED")


# ── Choke point 2: LLMGateway.call_agent redacts before transport ────────────


def _gateway_with_stubbed_transport(monkeypatch) -> tuple[LLMGateway, list[dict]]:
    """A gateway whose transport records the (system, prompt) it would send."""
    sent: list[dict] = []

    def _fake_openai(self, model, system, prompt, max_tokens, temperature):
        sent.append({"model": model, "system": system, "prompt": prompt})
        return ChatCompletion.model_validate({
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "{\"ok\": true}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2},
        })

    cfg = RuntimeConfig(database_url="postgresql://x/y")
    gw = LLMGateway.from_provider("openai", api_key="test-key", config=cfg)
    monkeypatch.setattr(LLMGateway, "_call_openai", _fake_openai)
    return gw, sent


def test_call_agent_redacts_secret_before_transport(monkeypatch):
    gw, sent = _gateway_with_stubbed_transport(monkeypatch)
    secret_prompt = (
        "Draft a proposal for this tension. The submitter says their key is "
        "sk-abc123def456ghi789jkl012"
    )
    resp = gw.call_agent("participant", secret_prompt, use_cache=False)
    assert resp.text == '{"ok": true}'
    assert len(sent) == 1
    assert "sk-abc123def456ghi789jkl012" not in sent[0]["prompt"]
    assert "[redacted:api-key]" in sent[0]["prompt"]


def test_call_agent_redacts_secret_in_system_prompt(monkeypatch):
    gw, sent = _gateway_with_stubbed_transport(monkeypatch)
    gw.call_agent(
        "participant", "an ordinary prompt",
        system="You are... Bearer abcdef1234567890abcdef",
        use_cache=False,
    )
    assert "abcdef1234567890abcdef" not in sent[0]["system"]
    assert "[redacted:bearer-token]" in sent[0]["system"]


def test_call_agent_clean_prompt_untouched(monkeypatch):
    gw, sent = _gateway_with_stubbed_transport(monkeypatch)
    gw.call_agent("participant", "State your position on this proposal.",
                  use_cache=False)
    assert sent[0]["prompt"] == "State your position on this proposal."


# ── Choke point 3: EndpointAdapter redacts before the federation POST ────────


def _endpoint_adapter_with_mock_transport():
    """An EndpointAdapter on a MockTransport that records request bodies."""
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"text": '{"position": "consent"}'})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    adapter = EndpointAdapter(
        ref=AgentRef(instance_id="kimberim", role=AgentRole.PARTICIPANT,
                     display_name="fed"),
        system_prompt="You are a federated participant.",
        endpoint="https://agent.example.com/olon",
        client=client,
    )
    return adapter, requests


def test_endpoint_adapter_redacts_prompt_before_post():
    """Federation is the exfiltration channel BY CONSTRUCTION — the endpoint
    operator reads the prompt verbatim. Secret-shaped content never leaves."""
    adapter, requests = _endpoint_adapter_with_mock_transport()
    out = adapter.respond(
        f"Evaluate this. Leaked credential follows: {_PRIVATE_KEY}",
        context="context with Bearer abcdef1234567890abcdef inside",
    )
    assert json.loads(out)["position"] == "consent"
    assert len(requests) == 1
    body = json.loads(requests[0].content.decode())
    assert "MIIEow" not in body["prompt"]
    assert "[redacted:private-key]" in body["prompt"]
    assert "abcdef1234567890abcdef" not in body["context"]
    assert "[redacted:bearer-token]" in body["context"]


def test_invariant_text_is_written_down():
    """The invariant exists as a first-class constant (docs/SECURITY.md carries
    the operational version; this is the in-code contract marker)."""
    assert PROMPT_DATA_INVARIANT.startswith("Nothing the platform wouldn't publish")
    assert "never enter prompts" in PROMPT_DATA_INVARIANT
    assert "federat" in PROMPT_DATA_INVARIANT
