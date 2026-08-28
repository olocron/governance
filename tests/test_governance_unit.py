"""G1 governance tests: the Chief Governance Agent surface (StubAgent-driven).

Sprint G1 per ROADMAP_V2: delegated attestation (bounded, recommend-only),
the attestation queue, the daily governance digest (counts-as-facts), and
the triage permission gate. All LLM calls are stubbed — no live provider.
DB-backed tests use a throwaway Postgres database (test_triage_unit pattern).
"""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session as SMSession

from olon.agents import ChiefGovernanceAgent, StubAgent
from olon.config import load_instance_config
from olon.schema import AgentRole
from olon.store import (
    apply_migrations,
    attest_agent,
    list_ledger_events,
    make_engine,
    raise_tension,
    register_agent,
)
from olon.utils import extract_json

load_dotenv()

_HAS_DB = bool(os.getenv("DATABASE_URL"))
FOUNDER_TOKEN = "g1-test-founder-token"
CGA_TOKEN = "g1-test-cga-token"

# ── Throwaway-DB fixture (test_triage_unit pattern) ───────────────────────────


def _maintenance_url(db_url: str) -> str:
    parts = urlparse(db_url)
    return urlunparse(parts._replace(path="/postgres"))


def _throwaway_url(db_url: str, dbname: str) -> str:
    parts = urlparse(db_url)
    return urlunparse(parts._replace(path=f"/{dbname}"))


def _autocommit_engine(database_url: str):
    url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, isolation_level="AUTOCOMMIT")


@pytest.fixture
def db(monkeypatch):
    """A throwaway database + a TestClient pointed at it.

    Yields (client, engine). The client's env (DATABASE_URL, tokens) is
    monkeypatched so every request hits the throwaway DB and the test tokens.
    """
    real_url = os.environ["DATABASE_URL"]
    dbname = f"olon_g1_{uuid.uuid4().hex[:8]}"
    throwaway = _throwaway_url(real_url, dbname)

    maint = _autocommit_engine(_maintenance_url(real_url))
    with maint.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    maint.dispose()

    apply_migrations(throwaway)
    eng = make_engine(throwaway)
    monkeypatch.setenv("DATABASE_URL", throwaway)
    monkeypatch.setenv("HARNESS_FOUNDER_TOKEN", FOUNDER_TOKEN)
    monkeypatch.setenv("HARNESS_CGA_TOKEN", CGA_TOKEN)
    from olon.api.server import app

    # A unique client IP per test: the S8 write limiter is per-IP process-wide
    # (20 writes/60s) and the app is a module singleton shared across tests —
    # without this, later tests would 429 on earlier tests' writes.
    client = TestClient(
        app, headers={"X-Forwarded-For": f"203.0.113.{uuid.uuid4().int % 250 + 1}"}
    )
    try:
        yield client, eng
    finally:
        eng.dispose()
        maint = _autocommit_engine(_maintenance_url(real_url))
        with maint.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": dbname},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        maint.dispose()


def _register_api(client, **fields) -> str:
    """Register an agent via the HTTP API (resolves its ABAC cell)."""
    body = {"display_name": f"g1-{uuid.uuid4().hex[:8]}", **fields}
    r = client.post("/instances/kimberim/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()["agent_id"]


def _stub_cga(monkeypatch, responder):
    """Patch the CGA the governance module instantiates with a StubAgent."""
    monkeypatch.setattr(
        "olon.api.governance.ChiefGovernanceAgent",
        lambda instance_id: StubAgent(responder, role=AgentRole.CHIEF_GOVERNANCE_AGENT),
    )


def _founder_row(s: SMSession):
    """A founder registry row with its resolved ABAC cell (triage included)."""
    return register_agent(
        s, instance_id="kimberim", display_name="Adrian", role="founder",
        stakeholder_type="founder",
        permissions={"submit", "triage", "deliberate", "vote", "veto", "admit"},
        attested=True,
    )


# ── 1. The role itself ───────────────────────────────────────────────────────


def test_cga_role_and_prompt_contract():
    """The CGA is a first-class staff role; its prompt encodes impartiality,
    process-authority-only, and the H11 no-counts rule."""
    cga = ChiefGovernanceAgent(instance_id="kimberim")
    assert cga.role == AgentRole.CHIEF_GOVERNANCE_AGENT
    assert cga.role == "chief-governance-agent"
    p = cga.system_prompt
    assert "impartial" in p.lower()
    assert "never" in p and "decide" in p.lower()
    assert "counts" in p.lower()  # H11: never state counts
    assert "JSON" in p


def test_cga_prompt_is_prompt_data_invariant_clean():
    """Nothing secret-shaped ships in the CGA's system prompt (H12)."""
    from olon.security import scan_secrets

    assert scan_secrets(ChiefGovernanceAgent.system_prompt) == []


def test_kimberim_delegation_config():
    """The kimberim instance ships delegation enabled, recommend-only, with
    the first-class cells (founder/traditional-owners) outside the CGA's
    bounds and the daily digest on."""
    ic = load_instance_config("kimberim")
    d = ic.governance.attestation_delegation
    assert d is not None and d.enabled is True
    assert d.auto_attest is False  # recommend-only in G1
    assert "staff" in d.allowed_stakeholder_types
    assert "founder" not in d.allowed_stakeholder_types
    assert "traditional-owners" not in d.allowed_stakeholder_types
    assert d.max_per_day > 0
    assert ic.governance.digest_interval_h == 24


# ── 2. Store: attestation is now a public, attributed act ────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_attest_agent_records_ledger_events(db):
    _, eng = db
    with SMSession(eng) as s:
        row = register_agent(s, instance_id="kimberim", display_name="Ledger Probe",
                             stakeholder_type="staff")
        s.commit()
        aid = row.agent_id

        attest_agent(s, agent_id=aid, attested=True, attested_by="cga")
        s.commit()
        grants = list_ledger_events(s, instance_id="kimberim",
                                    event_type="agent-attested")
        assert grants and grants[0].payload and json.loads(grants[0].payload) == {
            "agent_id": str(aid),
            "display_name": "Ledger Probe",
            "actor": "cga",
            "prior": False,
        }

        # Re-attesting the same state records no echo (the ledger records acts).
        attest_agent(s, agent_id=aid, attested=True, attested_by="founder")
        s.commit()
        assert len(list_ledger_events(s, instance_id="kimberim",
                                      event_type="agent-attested")) == 1

        attest_agent(s, agent_id=aid, attested=False, attested_by="founder")
        s.commit()
        revokes = list_ledger_events(s, instance_id="kimberim",
                                     event_type="agent-attestation-revoked")
        assert len(revokes) == 1
        assert json.loads(revokes[0].payload)["actor"] == "founder"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_list_ledger_events_filters_newest_first(db):
    _, eng = db
    with SMSession(eng) as s:
        a = register_agent(s, instance_id="kimberim", display_name="A")
        b = register_agent(s, instance_id="kimberim", display_name="B")
        s.flush()
        attest_agent(s, agent_id=a.agent_id, attested=True)
        attest_agent(s, agent_id=b.agent_id, attested=True)
        s.commit()
        rows = list_ledger_events(s, instance_id="kimberim",
                                  event_type="agent-attested")
        assert len(rows) == 2
        assert rows[0].sequence > rows[1].sequence  # newest first
        # An instance with no events at all:
        assert list_ledger_events(s, instance_id="no-such") == []


# ── 3. Delegated attestation via the API ─────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_cga_token_attests_within_bounds(db):
    client, eng = db
    aid = _register_api(client, stakeholder_type="staff", functional_domain="energy")
    r = client.post(
        f"/instances/kimberim/agents/{aid}/attest",
        json={"attested": True},
        headers={"Authorization": f"Bearer {CGA_TOKEN}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attested"] is True and body["attested_by"] == "cga"
    assert "triage" in body["effective_permissions"]  # staff cell unlocked
    with SMSession(eng) as s:
        assert json.loads(list_ledger_events(
            s, instance_id="kimberim", event_type="agent-attested")[0].payload
        )["actor"] == "cga"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_cga_blocked_outside_bounds_and_cannot_revoke(db):
    client, _ = db
    # First-class cell outside the CGA's bounds → structured escalation.
    to_id = _register_api(client, stakeholder_type="traditional-owners")
    r = client.post(
        f"/instances/kimberim/agents/{to_id}/attest",
        json={"attested": True},
        headers={"Authorization": f"Bearer {CGA_TOKEN}"},
    )
    assert r.status_code == 403
    assert r.json()["escalate_to"] == "founder"

    # The founder CAN attest it (full power, unchanged).
    r = client.post(
        f"/instances/kimberim/agents/{to_id}/attest",
        json={"attested": True},
        headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"},
    )
    assert r.status_code == 200 and r.json()["attested_by"] == "founder"

    # Revocation is NOT delegable — even for an in-bounds agent.
    staff_id = _register_api(client, stakeholder_type="staff")
    r = client.post(
        f"/instances/kimberim/agents/{staff_id}/attest",
        json={"attested": False},
        headers={"Authorization": f"Bearer {CGA_TOKEN}"},
    )
    assert r.status_code == 403
    assert r.json()["escalate_to"] == "founder"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_cga_daily_cap_enforced(db):
    client, eng = db
    ic = load_instance_config("kimberim")
    cap = ic.governance.attestation_delegation.max_per_day
    with SMSession(eng) as s:
        for _ in range(cap):
            row = register_agent(s, instance_id="kimberim",
                                 display_name=f"cap-{uuid.uuid4().hex[:6]}",
                                 stakeholder_type="staff")
            s.flush()
            attest_agent(s, agent_id=row.agent_id, attested=True, attested_by="cga")
        s.commit()
    over = _register_api(client, stakeholder_type="staff")
    r = client.post(
        f"/instances/kimberim/agents/{over}/attest",
        json={"attested": True},
        headers={"Authorization": f"Bearer {CGA_TOKEN}"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["max_per_day"] == cap and body["used_past_24h"] == cap

    # …and the founder is not bound by the CGA's cap.
    r = client.post(
        f"/instances/kimberim/agents/{over}/attest",
        json={"attested": True},
        headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"},
    )
    assert r.status_code == 200


# ── 4. The attestation queue ─────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_queue_facts_are_public_assessment_is_gated(db, monkeypatch):
    client, _ = db
    _register_api(client, stakeholder_type="staff")
    _register_api(client, stakeholder_type="traditional-owners")

    # Facts-only queue: public, no token, no LLM.
    r = client.get("/instances/kimberim/governance/attestation-queue")
    assert r.status_code == 200
    body = r.json()
    assert body["pending_count"] == 2
    assert body["delegation"]["enabled"] is True
    assert body["delegation"]["used_past_24h"] == 0
    by_type = {e["stakeholder_type"]: e for e in body["queue"]}
    assert by_type["staff"]["within_bounds"] is True       # code-computed
    assert by_type["traditional-owners"]["within_bounds"] is False
    assert all("cga_assessment" not in e for e in body["queue"])

    # ?assess=true without a staff token → 403 (it spends LLM budget).
    r = client.get("/instances/kimberim/governance/attestation-queue?assess=true")
    assert r.status_code == 403


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_queue_assessment_with_stubbed_cga(db, monkeypatch):
    client, _ = db
    _register_api(client, stakeholder_type="staff",
                  capability="grid integration modelling")

    seen: list[str] = []

    def responder(prompt: str, _ctx: str) -> str:
        seen.append(prompt)
        return json.dumps({"recommend": "attest",
                           "reasons": ["coherent staff registration"]})

    _stub_cga(monkeypatch, responder)
    r = client.get(
        "/instances/kimberim/governance/attestation-queue?assess=true",
        headers={"Authorization": f"Bearer {CGA_TOKEN}"},
    )
    assert r.status_code == 200
    entry = r.json()["queue"][0]
    assert entry["cga_assessment"]["recommend"] == "attest"
    assert entry["cga_assessment"]["reasons"] == ["coherent staff registration"]
    # S8: the untrusted capability text is fenced in the prompt, not bare.
    assert "[[[UNTRUSTED" in seen[0]
    assert "grid integration modelling" in seen[0]


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_queue_assessment_degrades_on_llm_failure(db, monkeypatch):
    client, _ = db
    _register_api(client, stakeholder_type="staff")

    def responder(_p, _c):
        raise RuntimeError("provider down")

    _stub_cga(monkeypatch, responder)
    r = client.get(
        "/instances/kimberim/governance/attestation-queue?assess=true",
        headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"},
    )
    assert r.status_code == 200  # never a 500 — the queue stands on facts
    entry = r.json()["queue"][0]
    assert entry["cga_assessment"]["recommend"] == "review"
    assert entry["cga_assessment"]["reasons"] == [
        "assessment unavailable (agent call failed)"
    ]


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_queue_assessment_normalizes_bad_output(db, monkeypatch):
    client, _ = db
    _register_api(client, stakeholder_type="staff")
    _stub_cga(monkeypatch, lambda _p, _c: 'garbage not json')
    r = client.get(
        "/instances/kimberim/governance/attestation-queue?assess=true",
        headers={"Authorization": f"Bearer {CGA_TOKEN}"},
    )
    assert r.status_code == 200
    a = r.json()["queue"][0]["cga_assessment"]
    assert a["recommend"] == "review"  # unknown → the safe default
    assert a["reasons"] == []


# ── 5. The governance digest ─────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_digest_counts_are_code_computed_themes_are_llm(db, monkeypatch):
    client, eng = db
    _register_api(client, stakeholder_type="staff")     # 1 pending
    with SMSession(eng) as s:
        f = _founder_row(s)
        s.flush()
        raise_tension(s, instance_id="kimberim", raised_by_agent_id=f.agent_id,
                      title="open tension", description="d")
        raise_tension(s, instance_id="kimberim", raised_by_agent_id=f.agent_id,
                      title="another open tension", description="d")
        s.commit()

    def responder(_p, _c):
        return json.dumps({"themes": ["attestation backlog present"],
                           "needs_human_eye": ["review the pending staff agent"]})

    _stub_cga(monkeypatch, responder)
    r = client.post("/instances/kimberim/governance/digest",
                    headers={"Authorization": f"Bearer {CGA_TOKEN}"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["facts"]["attestations"]["pending_count"] == 1
    assert d["facts"]["tensions"]["by_status"].get("open") == 2
    assert d["themes"] == ["attestation backlog present"]
    assert "agents awaiting attestation" in " ".join(d["needs_human_eye"])
    assert d["framing"].startswith("Counts are observed facts")
    assert d["cga"] == "ok"

    # Recorded to the ledger, servable via the public latest endpoint.
    with SMSession(eng) as s:
        rows = list_ledger_events(s, instance_id="kimberim",
                                  event_type="governance-digest")
        assert len(rows) == 1
    r = client.get("/instances/kimberim/governance/digest/latest")
    assert r.status_code == 200
    assert r.json()["digest"]["facts"]["attestations"]["pending_count"] == 1

    # A second digest windows from the first (the ledger grows, window narrows).
    r = client.post("/instances/kimberim/governance/digest",
                    headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 200
    assert r.json()["facts"]["window"]["since"] is not None


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_digest_llm_never_authors_facts(db, monkeypatch):
    """H11 tripwire: a rogue/malformed CGA response cannot inject counts,
    non-strings, or replace the code-computed flags."""
    client, eng = db
    _register_api(client, stakeholder_type="staff")
    _stub_cga(monkeypatch, lambda _p, _c: json.dumps({
        "themes": ["everyone supports the proposal", 42, None, "  "],
        "needs_human_eye": ["majority wants X"],
        "facts": {"attestations": {"pending_count": 999}},  # ignored
    }))
    r = client.post("/instances/kimberim/governance/digest",
                    headers={"Authorization": f"Bearer {CGA_TOKEN}"})
    assert r.status_code == 200
    d = r.json()
    assert d["facts"]["attestations"]["pending_count"] == 1  # code's number stands
    assert all(isinstance(t, str) and t for t in d["themes"])
    assert 42 not in d["themes"] and None not in d["themes"]
    assert "majority wants X" in d["needs_human_eye"]  # strings pass through…
    assert any("awaiting attestation" in f for f in d["needs_human_eye"])  # …code flags stay


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_digest_degrades_and_records_without_llm(db, monkeypatch):
    client, eng = db
    _register_api(client, stakeholder_type="staff")

    def responder(_p, _c):
        raise RuntimeError("provider outage")

    _stub_cga(monkeypatch, responder)
    r = client.post("/instances/kimberim/governance/digest",
                    headers={"Authorization": f"Bearer {CGA_TOKEN}"})
    assert r.status_code == 200  # the day's facts are recorded regardless
    d = r.json()
    assert d["themes"] == []
    assert "unavailable" in d["cga"]
    with SMSession(eng) as s:
        assert list_ledger_events(s, instance_id="kimberim",
                                  event_type="governance-digest")


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_digest_endpoints_auth_and_404(db):
    client, _ = db
    r = client.post("/instances/kimberim/governance/digest")
    assert r.status_code == 403
    r = client.post("/instances/kimberim/governance/digest",
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403
    r = client.get("/instances/no-such/governance/digest/latest")
    assert r.status_code == 404
    r = client.get("/instances/kimberim/governance/digest/latest")
    assert r.status_code == 404  # nothing recorded yet in this throwaway DB


# ── 6. Triage oversight: the documented permission gate, enforced ────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_triage_requires_triage_permission(db, monkeypatch):
    client, eng = db
    with SMSession(eng) as s:
        f = _founder_row(s)
        s.flush()
        t = raise_tension(s, instance_id="kimberim", raised_by_agent_id=f.agent_id,
                          title="t", description="d")
        s.commit()
        tid, fid = t.id, f.agent_id

    url = f"/instances/kimberim/tensions/{tid}/triage"

    # No caller → 403 (the call spends LLM budget and writes the record).
    r = client.post(url)
    assert r.status_code == 403
    # Un-attested caller → 403.
    unatt = _register_api(client, stakeholder_type="staff")
    r = client.post(f"{url}?triggered_by={unatt}")
    assert r.status_code == 403
    # Attested but no `triage` permission (participant default cell) → 403.
    voter = _register_api(client)  # no stakeholder_type → participant default
    client.post(f"/instances/kimberim/agents/{voter}/attest",
                json={"attested": True},
                headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    r = client.post(f"{url}?triggered_by={voter}")
    assert r.status_code == 403 and "triage" in r.json()["error"]

    # Attested staff (triage permission) → runs, with a stubbed Guardian.
    monkeypatch.setattr(
        "olon.api.routes.TriageGuardian",
        lambda instance_id: StubAgent(
            json.dumps({"on_domain": True, "materiality": "high",
                        "duplicate_of": None, "notes": "ok",
                        "suggested_priority": 40}),
            role=AgentRole.TRIAGE_GUARDIAN),
    )
    r = client.post(f"{url}?triggered_by={fid}")
    assert r.status_code == 200, r.text
    assert r.json()["assessment"]["materiality"] == "high"


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_triage_llm_failure_is_retryable_503(db, monkeypatch):
    client, eng = db
    with SMSession(eng) as s:
        f = _founder_row(s)
        s.flush()
        t = raise_tension(s, instance_id="kimberim", raised_by_agent_id=f.agent_id,
                          title="t", description="d")
        s.commit()
        tid, fid = t.id, f.agent_id

    def boom(_p, _c):
        raise RuntimeError("provider down")

    monkeypatch.setattr(
        "olon.api.routes.TriageGuardian",
        lambda instance_id: StubAgent(boom, role=AgentRole.TRIAGE_GUARDIAN),
    )
    r = client.post(f"/instances/kimberim/tensions/{tid}/triage?triggered_by={fid}")
    assert r.status_code == 503
    assert "untouched" in r.json()["error"]
    # The tension is indeed untouched.
    d = client.get(f"/instances/kimberim/tensions/{tid}").json()
    assert d["status"] == "open" and d["triage"] is None


# ── 7. Scheduler ─────────────────────────────────────────────────────────────


def test_digest_instances_reads_config():
    from olon.api.scheduler import _digest_instances

    out = dict(_digest_instances())
    assert out.get("kimberim") == 24.0


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_digest_due_is_ledger_driven(db, monkeypatch):
    from olon.api.governance import build_governance_digest
    from olon.api.scheduler import _digest_due

    ic = load_instance_config("kimberim")
    _, eng = db
    with SMSession(eng) as s:
        assert _digest_due("kimberim", eng, interval_h=24.0) is True  # none yet
        build_governance_digest(s, ic=ic, with_themes=False)
        s.commit()
        assert _digest_due("kimberim", eng, interval_h=24.0) is False
        # Age the recorded digest past the interval → due again.
        s.execute(text(
            "UPDATE ledger_event SET created_at = created_at - interval '25 hours' "
            "WHERE event_type = 'governance-digest'"
        ))
        s.commit()
        assert _digest_due("kimberim", eng, interval_h=24.0) is True


# ── 8. extract_json backstop used by the CGA call sites ──────────────────────


def test_extract_json_tolerates_cga_style_output():
    assert extract_json('{"recommend": "attest"} prose after') == {
        "recommend": "attest"
    }
    assert extract_json("```json\n{\"themes\": []}\n```") == {"themes": []}
    assert extract_json("no json at all") == {}
