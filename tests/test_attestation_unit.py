"""S9 attestation-tier tests: submit-only until the founder attests.

Covers the three closed risks:
  1. Sybil capture — mass registration buys nothing (no vote/deliberate/trigger).
  2. Weight self-claiming — 'traditional-owners' 2.0 capped at 1.0 until attested.
  3. Economic DoS — un-attested agents excluded from platform-gateway cycles;
     epoch/deliberation triggering gated to attested action-holders.

DB-backed tests use the dev database (same pattern as the other unit suites).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as SMSession

from olon.config import load_runtime_config
from olon.store import (
    attest_agent,
    effective_permissions,
    effective_weight,
    get_agent,
    make_engine,
    register_agent,
)

FOUNDER_TOKEN = "test-founder-token-xyz"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("HARNESS_FOUNDER_TOKEN", FOUNDER_TOKEN)
    from olon.api.server import app

    return TestClient(app)


def _register(s, **kw):
    return register_agent(s, instance_id="kimberim", **kw)


# ── 1. Store: effective permissions / weight ──────────────────────────────────


def test_unattested_is_submit_only():
    row = type("R", (), {
        "attested": False, "permissions": '["submit","deliberate","vote","veto"]',
        "weight": 2.0,
    })()
    assert effective_permissions(row) == {"submit"}
    assert effective_weight(row) == 1.0


def test_attested_gets_full_cell():
    row = type("R", (), {
        "attested": True, "permissions": '["submit","deliberate","vote"]',
        "weight": 2.0,
    })()
    assert effective_permissions(row) == {"submit", "deliberate", "vote"}
    assert effective_weight(row) == 2.0


def test_attest_agent_toggles():
    eng = make_engine(load_runtime_config().database_url)
    with SMSession(eng) as s:
        row = _register(s, display_name=f"attest-toggle-{uuid4().hex[:8]}")
        s.commit()
        aid = row.agent_id
    with SMSession(eng) as s:
        got = attest_agent(s, agent_id=aid, attested=True)
        s.commit()
        assert got.attested and effective_weight(got) == got.weight
    with SMSession(eng) as s:
        got = attest_agent(s, agent_id=aid, attested=False)
        s.commit()
        assert not got.attested and effective_permissions(got) == {"submit"}


# ── 2. API: registration starts un-attested; detail is transparent ──────────


def test_registration_starts_submit_only(client):
    r = client.post(
        "/instances/kimberim/agents",
        json={"display_name": "Sybil Probe",
              "stakeholder_type": "traditional-owners"},
        headers={"X-Forwarded-For": "203.0.113.88"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["attested"] is False
    assert body["effective_permissions"] == ["submit"]
    assert body["weight"] == 2.0  # the CLAIMED cell is still reported…

    d = client.get(f"/instances/kimberim/agents/{body['agent_id']}").json()
    assert d["effective_permissions"] == ["submit"]  # …but only submit applies
    assert d["effective_weight"] == 1.0  # and the claim is capped


# ── 3. API: epoch + deliberation triggering is gated ─────────────────────────


def test_anonymous_cannot_trigger_epoch(client):
    r = client.post("/instances/kimberim/epochs")
    assert r.status_code == 403
    assert "attested" in r.json()["error"] or "triggered_by" in r.json()["error"]


def test_anonymous_cannot_trigger_deliberation(client):
    r = client.post("/instances/kimberim/deliberations")
    assert r.status_code == 403


def test_unattested_agent_cannot_trigger_epoch(client):
    eng = make_engine(load_runtime_config().database_url)
    with SMSession(eng) as s:
        row = _register(s, display_name=f"unatt-trig-{uuid4().hex[:8]}",
                        stakeholder_type="staff")
        s.commit()
        aid = row.agent_id
    r = client.post(f"/instances/kimberim/epochs?triggered_by={aid}")
    assert r.status_code == 403
    assert "attest" in r.json()["error"]


def test_attested_agent_can_trigger_epoch(client):
    eng = make_engine(load_runtime_config().database_url)
    with SMSession(eng) as s:
        row = _register(s, display_name=f"att-trig-{uuid4().hex[:8]}",
                        stakeholder_type="staff")
        s.commit()
        aid = row.agent_id
    with SMSession(eng) as s:
        attest_agent(s, agent_id=aid, attested=True)
        s.commit()
    # 202 = accepted (an epoch actually fires; the worker runs detached).
    r = client.post(f"/instances/kimberim/epochs?triggered_by={aid}")
    assert r.status_code in (202, 409)  # 409 if one is already running


# ── 4. API: the attest endpoint ───────────────────────────────────────────────


def test_attest_endpoint_requires_token(client):
    eng = make_engine(load_runtime_config().database_url)
    with SMSession(eng) as s:
        row = _register(s, display_name=f"att-ep-{uuid4().hex[:8]}")
        s.commit()
        aid = row.agent_id
    # No token -> 403
    r = client.post(f"/instances/kimberim/agents/{aid}/attest")
    assert r.status_code == 403
    # Wrong token -> 403
    r = client.post(
        f"/instances/kimberim/agents/{aid}/attest",
        json={"attested": True},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 403
    # Right token -> unlocks the full claimed cell
    r = client.post(
        f"/instances/kimberim/agents/{aid}/attest",
        json={"attested": True},
        headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["attested"] is True
    assert "vote" in body["effective_permissions"]

    # Revoke restores submit-only.
    r = client.post(
        f"/instances/kimberim/agents/{aid}/attest",
        json={"attested": False},
        headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["effective_permissions"] == ["submit"]


def test_attest_endpoint_disabled_without_token(monkeypatch):
    monkeypatch.setenv("HARNESS_FOUNDER_TOKEN", "")
    from importlib import reload
    import olon.config as cfg_mod
    reload(cfg_mod)  # pick up env… (load_runtime_config reads env at call time anyway)
    from olon.api.server import app

    client = TestClient(app)
    r = client.post(f"/instances/kimberim/agents/{uuid4()}/attest")
    assert r.status_code == 503


# ── 5. Live wiring: un-attested bare agents are excluded from cycles ─────────


def test_unattested_bare_agent_excluded():
    from olon.api.live import _participant_agent

    row = type("R", (), {
        "agent_id": uuid4(), "display_name": "Bare", "attested": False,
        "model": "", "endpoint": "", "api_key_enc": "", "adapter": None,
        "capability": "x", "owner": "",
    })()
    assert _participant_agent(row, "kimberim") is None


def test_unattested_own_transport_included_at_weight_1():
    from olon.api.live import _participant_agent

    row = type("R", (), {
        "agent_id": uuid4(), "display_name": "OwnKey", "attested": False,
        "model": "glm-5-turbo", "endpoint": "", "api_key_enc": "k",
        "adapter": "provider", "capability": "x", "owner": "",
        # claimed 2.0 type — must be capped while un-attested
        "weight": 2.0,
    })()
    a = _participant_agent(row, "kimberim")
    assert a is not None
    assert a.ref.weight == 1.0


def test_attested_bare_agent_uses_platform_gateway():
    from olon.api.live import _participant_agent

    row = type("R", (), {
        "agent_id": uuid4(), "display_name": "Trusted", "attested": True,
        "model": "", "endpoint": "", "api_key_enc": "", "adapter": None,
        "capability": "x", "owner": "", "weight": 2.0,
    })()
    a = _participant_agent(row, "kimberim")
    assert a is not None and a.ref.weight == 2.0
