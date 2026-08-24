"""H10 backlog-flooding tests: same-submitter dedup (park) + open-tension cap.

Pure-function tests for the screening policy (normalize/similarity/
screen_intake — no DB), plus DB-gated store/route-level tests that verify
parked tensions are recorded, visible, and NEVER served by next_tension.

The park-not-delete property is asserted throughout: a parked tension still
has its ledger event (with the park reason), still lists at GET /tensions,
and can still be fetched by id — only queue eligibility changes.
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

from olon.intake import (
    MAX_OPEN_PER_SUBMITTER,
    SIMILARITY_THRESHOLD,
    normalize,
    screen_intake,
    similarity,
)
from olon.store import (
    apply_migrations,
    list_backlog,
    make_engine,
    next_tension,
    raise_tension,
    register_agent,
)

load_dotenv()

_HAS_DB = bool(os.getenv("DATABASE_URL"))


# ── Pure: normalize + similarity ──────────────────────────────────────────────


def test_normalize_flattens_case_punct_and_whitespace():
    assert normalize("Grid-Export vs. COMPUTE!") == "grid export vs compute"
    assert normalize("  Multiple\n\tspaces   ") == "multiple spaces"


def test_similarity_identical_and_unrelated():
    a = "Grid export revenue crowds out the compute value-add"
    assert similarity(a, a) == 1.0
    # Trivial rewording still scores high (the flood case).
    reworded = "Grid export revenue crowds out the compute value add!"
    assert similarity(a, reworded) >= SIMILARITY_THRESHOLD
    # Genuinely different tensions score low.
    assert similarity(a, "Water licensing for the solar updraft tower") < 0.5


def test_similarity_empty_inputs_are_zero():
    assert similarity("", "anything") == 0.0
    assert similarity("anything", "") == 0.0


# ── Pure: screen_intake policy ────────────────────────────────────────────────


class _Row:
    """A minimal stand-in for TensionRow (screen_intake reads id/title/
    description/status only — kept pure so the policy is testable without DB)."""

    def __init__(self, title: str, description: str, status: str = "open"):
        self.id = uuid.uuid4()
        self.title = title
        self.description = description
        self.status = status


_TITLE = "Grid export crowds out compute"
_DESC = "Maximising export revenue may starve the on-site compute value-add."


def test_screen_accepts_when_no_history():
    d = screen_intake([], title=_TITLE, description=_DESC)
    assert d.parked is False
    assert d.reason is None


def test_screen_parks_near_duplicate_from_same_submitter():
    own = [_Row(_TITLE, _DESC)]
    # Trivial rewording of the same tension.
    reworded = "Maximising export revenue may starve the on site compute value add"
    d = screen_intake(own, title=_TITLE + "!", description=reworded)
    assert d.parked is True
    assert d.reason == "duplicate"
    assert d.duplicate_of == own[0].id


def test_screen_accepts_same_text_from_a_different_angle():
    # A distinct tension from the same submitter is NOT a duplicate.
    own = [_Row(_TITLE, _DESC)]
    d = screen_intake(
        own, title="Water table draw for tower cooling",
        description="Cooling demand may affect the aquifer recharge rate",
    )
    assert d.parked is False


def test_screen_parks_over_cap_and_exempt_founder():
    own = [_Row(f"t{i}", f"d{i}") for i in range(MAX_OPEN_PER_SUBMITTER)]
    d = screen_intake(own, title="one more", description="d")
    assert d.parked is True
    assert d.reason == "open-cap"
    # The founder is cap-exempt (sponsors anonymous submissions + seeds).
    df = screen_intake(own, title="one more", description="d", is_founder=True)
    assert df.parked is False


def test_screen_cap_counts_only_queue_statuses():
    # Parked/decided/scheduled rows don't consume queue seats.
    own = [_Row(f"t{i}", f"d{i}", status="parked") for i in range(MAX_OPEN_PER_SUBMITTER)]
    d = screen_intake(own, title="fresh", description="d")
    assert d.parked is False


def test_screen_dedup_matches_parked_siblings_too():
    # A flood that varies slightly still dedups against its parked siblings.
    own = [_Row(_TITLE, _DESC, status="parked")]
    d = screen_intake(own, title=_TITLE, description=_DESC)
    assert d.parked is True
    assert d.reason == "duplicate"
    assert d.duplicate_of == own[0].id


# ── DB-gated: parked tensions are recorded but never served ──────────────────


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
def db_session():
    """A fresh throwaway DB with migrations applied (test_backlog pattern)."""
    db_url = os.environ["DATABASE_URL"]
    dbname = f"olon_intake_{uuid.uuid4().hex[:8]}"
    throwaway = _throwaway_url(db_url, dbname)
    maint = _autocommit_engine(_maintenance_url(db_url))
    with maint.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    maint.dispose()
    apply_migrations(throwaway)
    eng = make_engine(throwaway)
    try:
        with SMSession(eng) as s:
            yield s
    finally:
        eng.dispose()
        maint = _autocommit_engine(_maintenance_url(db_url))
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


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_raise_tension_records_park_reason_in_ledger(db_session):
    """A parked raise still emits its tension-raised event, carrying the park
    reason + duplicate_of — the public record stays complete (park ≠ delete)."""
    s = db_session
    agent = register_agent(s, instance_id="kimberim", display_name="Flood Bot")
    s.flush()
    orig = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=agent.agent_id,
        title=_TITLE, description=_DESC,
    )
    s.flush()
    parked = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=agent.agent_id,
        title=_TITLE + "!", description=_DESC,
        status="parked", park_reason="duplicate", duplicate_of=orig.id,
    )
    s.commit()

    assert parked.status == "parked"
    # Both rows are fully recorded and readable.
    backlog = list_backlog(s, instance_id="kimberim")
    assert len(backlog) == 2
    # The ledger event for the parked raise carries the reason.
    ev = s.exec(
        text(  # plain SQL: avoid importing query helpers not meant for tests
            "SELECT payload FROM ledger_event "
            "WHERE event_type='tension-raised' ORDER BY sequence"
        )
    ).all()
    payloads = [json.loads(p[0]) for p in ev]
    parked_ev = next(p for p in payloads if p["tension_id"] == str(parked.id))
    assert parked_ev["parked"] is True
    assert parked_ev["park_reason"] == "duplicate"
    assert parked_ev["duplicate_of"] == str(orig.id)
    # Plain raises carry no parked key (back-compat shape).
    plain_ev = next(p for p in payloads if p["tension_id"] == str(orig.id))
    assert "parked" not in plain_ev


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_next_tension_never_serves_parked(db_session):
    """The flooding payoff is removed: parked rows never enter the queue, so
    100 near-duplicates cannot bury the one real tension."""
    s = db_session
    agent = register_agent(s, instance_id="kimberim", display_name="Flood Bot")
    real = raise_tension(
        s, instance_id="kimberim", raised_by_agent_id=agent.agent_id,
        title="Real tension", description="a genuine gap", priority=90,  # worst priority
    )
    # The flood: near-duplicates parked by intake screening.
    for i in range(10):
        raise_tension(
            s, instance_id="kimberim", raised_by_agent_id=agent.agent_id,
            title=f"Grid export crowds out compute {i}", description="flood variant",
            priority=1,  # best priority — must not matter, they're parked
            status="parked", park_reason="duplicate", duplicate_of=real.id,
        )
    s.commit()

    popped = next_tension(s, instance_id="kimberim")
    assert popped is not None
    assert popped.id == real.id, "parked flood rows must never be served"


# ── Route-level: the API parks and says so ────────────────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_submit_tension_parks_duplicate_via_api():
    """POST /tensions twice with near-identical text from the same agent:
    the second is 201 + parked (visible, reversible), never silently dropped."""
    from olon.api.server import create_app  # noqa: PLC0415

    client = TestClient(create_app())
    r1 = client.post("/instances/kimberim/agents", json={
        "display_name": "H10 Test Agent", "capability": "testing intake",
    })
    assert r1.status_code == 201
    agent_id = r1.json()["agent_id"]

    body = {"title": "Water licensing risk", "description": "Cooling may draw on the aquifer",
            "raised_by": agent_id}
    r2 = client.post("/instances/kimberim/tensions", json=body)
    assert r2.status_code == 201
    assert r2.json()["status"] == "open"
    assert "parked" not in r2.json()

    # Near-identical re-file → parked as duplicate of the first.
    r3 = client.post("/instances/kimberim/tensions", json={
        **body, "title": "Water licensing risk!",
        "description": "Cooling may draw on the aquifer.",
    })
    assert r3.status_code == 201
    out = r3.json()
    assert out["status"] == "parked"
    assert out["parked"] is True
    assert out["park_reason"] == "duplicate"
    assert out["duplicate_of"] == r2.json()["tension_id"]

    # Parked tensions remain readable at the detail endpoint.
    r4 = client.get(f"/instances/kimberim/tensions/{out['tension_id']}")
    assert r4.status_code == 200
    assert r4.json()["status"] == "parked"
