"""O1 doc-root tests: versioned documents, governed writes, private docs.

The shared document root (ROADMAP_V2 O1): append-only versions mirrored to
the ledger with actor; public read, staff-token write; private docs gated to
owner + founder/CGA. All deterministic — no LLM. DB-backed tests use a
throwaway Postgres (test_triage_unit / test_governance_unit pattern).
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session as SMSession

from olon.store import (
    apply_migrations,
    create_doc,
    get_doc,
    get_doc_version,
    list_doc_history,
    list_docs,
    list_ledger_events,
    make_engine,
    register_agent,
    update_doc,
)

load_dotenv()

_HAS_DB = bool(os.getenv("DATABASE_URL"))
FOUNDER_TOKEN = "o1-test-founder-token"
CGA_TOKEN = "o1-test-cga-token"

# ── Throwaway-DB fixture (shared pattern) ─────────────────────────────────────


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
    """Throwaway DB + TestClient pointed at it (unique client IP per test so
    the S8 write limiter never 429s a later test)."""
    real_url = os.environ["DATABASE_URL"]
    dbname = f"olon_o1_{uuid.uuid4().hex[:8]}"
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

    client = TestClient(
        app, headers={"X-Forwarded-For": f"198.51.100.{uuid.uuid4().int % 250 + 1}"}
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


# ── 1. Store: append-only versions + ledger mirroring ────────────────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_create_and_update_doc_versioning_and_ledger(db):
    _, eng = db
    with SMSession(eng) as s:
        doc = create_doc(s, instance_id="kimberim", slug="test-doc",
                         title="Test", content="v1 body",
                         change_note="initial")
        s.commit()
        assert doc.current_version == 1
        v1 = get_doc_version(s, doc_id=doc.id, version=1)
        assert v1.content == "v1 body" and v1.written_by == "founder"

        doc = update_doc(s, doc=doc, content="v2 body", written_by="cga",
                         change_note="clarify")
        s.commit()
        assert doc.current_version == 2
        # v1 is untouched (append-only), history newest-first.
        assert get_doc_version(s, doc_id=doc.id, version=1).content == "v1 body"
        hist = list_doc_history(s, doc_id=doc.id)
        assert [v.version for v in hist] == [2, 1]
        assert hist[0].written_by == "cga"

        created = list_ledger_events(s, instance_id="kimberim",
                                     event_type="doc-created")
        updated = list_ledger_events(s, instance_id="kimberim",
                                     event_type="doc-updated")
        assert len(created) == 1 and len(updated) == 1
        import json as _json
        assert _json.loads(updated[0].payload)["actor"] == "cga"
        assert _json.loads(updated[0].payload)["version"] == 2

        # Duplicate slug refused (None).
        assert create_doc(s, instance_id="kimberim", slug="test-doc",
                          title="Dup", content="x") is None


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_list_docs_visibility_filtering(db):
    _, eng = db
    with SMSession(eng) as s:
        owner = register_agent(s, instance_id="kimberim", display_name="Owner")
        other = register_agent(s, instance_id="kimberim", display_name="Other")
        s.flush()
        create_doc(s, instance_id="kimberim", slug="pub", title="P", content="p")
        create_doc(s, instance_id="kimberim", slug="priv", title="X",
                   content="x", visibility="private",
                   owner_agent_id=owner.agent_id)
        s.commit()

        slugs = [d.slug for d in list_docs(s, instance_id="kimberim")]
        assert "pub" in slugs and "priv" not in slugs          # default: public
        slugs = [d.slug for d in list_docs(s, instance_id="kimberim",
                                           include_private_for=owner.agent_id)]
        assert "priv" in slugs                                  # owner sees it
        slugs = [d.slug for d in list_docs(s, instance_id="kimberim",
                                           include_private_for=other.agent_id)]
        assert "priv" not in slugs                              # stranger doesn't
        slugs = [d.slug for d in list_docs(s, instance_id="kimberim",
                                           include_all_private=True)]
        assert "priv" in slugs                                  # staff sees all


# ── 2. API: writes are staff-gated, reads public/private-gated ───────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_api_create_update_requires_staff_token(db):
    client, eng = db
    # No token → 403.
    r = client.post("/instances/kimberim/docs", json={
        "slug": "scratch", "title": "Scratch", "content": "hello",
    })
    assert r.status_code == 403
    # Founder token → 201.
    r = client.post("/instances/kimberim/docs", json={
        "slug": "scratch", "title": "Scratch", "content": "hello",
        "change_note": "first",
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1

    # Duplicate slug → 409.
    r = client.post("/instances/kimberim/docs", json={
        "slug": "scratch", "title": "Again", "content": "x",
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 409

    # CGA token can append a version (staff write path).
    r = client.put("/instances/kimberim/docs/scratch", json={
        "content": "hello v2", "change_note": "edit via CGA",
    }, headers={"Authorization": f"Bearer {CGA_TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2 and body["written_by"] == "cga"

    # Public read of latest + explicit old version.
    r = client.get("/instances/kimberim/docs/scratch")
    assert r.status_code == 200 and r.json()["content"] == "hello v2"
    r = client.get("/instances/kimberim/docs/scratch/versions/1")
    assert r.status_code == 200 and r.json()["content"] == "hello"
    r = client.get("/instances/kimberim/docs/scratch/versions")
    assert [v["version"] for v in r.json()["versions"]] == [2, 1]

    # Bad slug rejected by validation.
    r = client.post("/instances/kimberim/docs", json={
        "slug": "Bad Slug!", "title": "X", "content": "x",
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 422
    # Unknown instance / doc.
    r = client.get("/instances/no-such/docs")
    assert r.status_code == 404
    r = client.get("/instances/kimberim/docs/nope")
    assert r.status_code == 404


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_api_private_doc_gating(db):
    client, eng = db
    with SMSession(eng) as s:
        owner = register_agent(s, instance_id="kimberim", display_name="Owner")
        s.commit()
        owner_id = owner.agent_id

    r = client.post("/instances/kimberim/docs", json={
        "slug": "ip-notes", "title": "IP Notes", "content": "secret sauce",
        "visibility": "private", "owner_agent_id": str(owner_id),
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 201

    # Stranger (different as_agent) → 403; anonymous → 403.
    r = client.get("/instances/kimberim/docs/ip-notes")
    assert r.status_code == 403
    r = client.get(
        "/instances/kimberim/docs/ip-notes",
        params={"as_agent": str(uuid.uuid4())},
    )
    assert r.status_code == 403
    # Owner → 200. Founder token → 200.
    r = client.get(
        "/instances/kimberim/docs/ip-notes", params={"as_agent": str(owner_id)},
    )
    assert r.status_code == 200 and r.json()["content"] == "secret sauce"
    r = client.get("/instances/kimberim/docs/ip-notes",
                   headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 200

    # The public list hides it; the owner's list shows it; staff sees all.
    r = client.get("/instances/kimberim/docs")
    assert "ip-notes" not in [d["slug"] for d in r.json()["docs"]]
    r = client.get("/instances/kimberim/docs", params={"as_agent": str(owner_id)})
    assert "ip-notes" in [d["slug"] for d in r.json()["docs"]]
    r = client.get("/instances/kimberim/docs",
                   headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert "ip-notes" in [d["slug"] for d in r.json()["docs"]]

    # Making it public is a recorded write (visibility change in the event).
    r = client.put("/instances/kimberim/docs/ip-notes", json={
        "content": "secret sauce", "change_note": "publish",
        "visibility": "public",
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 200
    with SMSession(eng) as s:
        ev = list_ledger_events(s, instance_id="kimberim",
                                event_type="doc-updated")[0]
        import json as _json
        payload = _json.loads(ev.payload)
        assert payload["visibility_changed"] is True
    r = client.get("/instances/kimberim/docs/ip-notes")  # now public
    assert r.status_code == 200


# ── 3. Serving: /docs/{file} from the doc root; static fallback ──────────────


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_docs_url_serves_doc_root_latest(db):
    """The acceptance demo: edit a seeded doc via the API and the change is
    live at /docs/<file> with no redeploy. Non-seeded files (PDF) fall
    through to the static repo copy."""
    client, eng = db
    # Seed into the throwaway DB (idempotent — same function the lifespan runs).
    from olon.api.docs import seed_doc_root
    from olon.api.server import DOCS_DIR
    seed_doc_root(DOCS_DIR)

    r = client.get("/docs/AGENT_PROTOCOL.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "OLOCRON Agent Protocol" in r.text or "agent protocol" in r.text.lower()

    # An API edit is immediately visible at the /docs URL.
    marker = f"o1-edit-{uuid.uuid4().hex[:6]}"
    r = client.put("/instances/kimberim/docs/agent-protocol", json={
        "content": f"# Protocol (test edit {marker})", "change_note": "test edit",
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 200
    r = client.get("/docs/AGENT_PROTOCOL.md")
    assert marker in r.text

    # Static fallback for a non-seeded file (the handbook PDF).
    r = client.get("/docs/olocron-participant-handbook.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    # Path traversal is refused.
    r = client.get("/docs/..%2F..%2Fpyproject.toml")
    assert r.status_code == 404

    # A seeded doc later made private serves 404 at /docs/<file> — never the
    # stale static repo copy (that would leak pre-privatisation content).
    r = client.put("/instances/kimberim/docs/roadmap", json={
        "content": "# private now", "change_note": "privatise",
        "visibility": "private",
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 200
    r = client.get("/docs/ROADMAP.md")
    assert r.status_code == 404


@pytest.mark.live
@pytest.mark.skipif(not _HAS_DB, reason="needs DATABASE_URL")
def test_seed_is_idempotent_and_preserves_edits(db):
    client, eng = db
    from olon.api.docs import seed_doc_root
    from olon.api.server import DOCS_DIR

    seed_doc_root(DOCS_DIR)
    with SMSession(eng) as s:
        n_after_first = len(list_docs(s, instance_id="kimberim"))
        road = get_doc(s, instance_id="kimberim", slug="roadmap-v2")
        assert road is not None and road.current_version == 1
    assert n_after_first >= 5  # the five authoritative docs

    # A manual edit survives reseeding (never overwrite).
    r = client.put("/instances/kimberim/docs/roadmap-v2", json={
        "content": "# ROADMAP v2 (edited)", "change_note": "manual edit",
    }, headers={"Authorization": f"Bearer {FOUNDER_TOKEN}"})
    assert r.status_code == 200

    seed_doc_root(DOCS_DIR)
    with SMSession(eng) as s:
        assert len(list_docs(s, instance_id="kimberim")) == n_after_first
        road = get_doc(s, instance_id="kimberim", slug="roadmap-v2")
        assert road.current_version == 2  # the edit stands
