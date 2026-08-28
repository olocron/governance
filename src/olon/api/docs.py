"""O1 — the shared document root API.

ROADMAP_V2 Arm III / O1: one authoritative home for everything the Olon
documents. Public read, governed write (founder/CGA token — consent-routing
of doc changes is G2), every change versioned append-only and mirrored to
the ledger with its actor. Private docs (the §3 IP clause) are readable by
their owner and the founder/CGA only; making a private doc public is itself
a recorded write.

REST:
  GET  /instances/{id}/docs                    — list (public; +owned private)
  GET  /instances/{id}/docs/{slug}             — latest content (private gated)
  GET  /instances/{id}/docs/{slug}/versions    — version history (gated)
  GET  /instances/{id}/docs/{slug}/versions/{n}— one version (gated)
  POST /instances/{id}/docs                    — create (staff token)
  PUT  /instances/{id}/docs/{slug}             — new version (staff token)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session as SMSession

from olon.api.governance import cga_authorized, founder_authorized
from olon.config import INSTANCES_DIR, load_instance_config, load_runtime_config
from olon.store import (
    create_doc,
    get_doc,
    get_doc_version,
    list_doc_history,
    list_docs,
    make_engine,
    update_doc,
)

log = logging.getLogger(__name__)

router = APIRouter()

# Field caps (public-ish surface; the 1 MB body cap is the outer backstop).
_MAX_CONTENT = 512 * 1024  # the protocol is ~27 KB — this is generous.
_SLUG_RE = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


# ── Request models ────────────────────────────────────────────────────────────


class DocCreate(BaseModel):
    slug: str = Field(..., pattern=_SLUG_RE, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=_MAX_CONTENT)
    visibility: Literal["public", "private"] = "public"
    owner_agent_id: UUID | None = None
    change_note: str = Field(default="", max_length=500)


class DocUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=_MAX_CONTENT)
    change_note: str = Field(default="", max_length=500)
    # Optional visibility change rides the same version event (recorded).
    visibility: Literal["public", "private"] | None = None


# ── Serialization ─────────────────────────────────────────────────────────────


def _doc_out(doc, version=None) -> dict:
    out = {
        "slug": doc.slug,
        "title": doc.title,
        "version": doc.current_version,
        "visibility": doc.visibility,
        "owner_agent_id": str(doc.owner_agent_id) if doc.owner_agent_id else None,
        "updated_at": doc.updated_at.isoformat(),
        "created_at": doc.created_at.isoformat(),
    }
    if version is not None:
        out["content"] = version.content
        out["change_note"] = version.change_note
        out["written_by"] = version.written_by
    return out


def _doc_readable(doc, request: Request, as_agent: UUID | None) -> bool:
    """Public docs read openly; private docs need the owner or staff auth."""
    if doc.visibility == "public":
        return True
    if founder_authorized(request) or cga_authorized(request):
        return True
    return as_agent is not None and doc.owner_agent_id == as_agent


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/instances/{instance_id}/docs")
async def list_docs_ep(
    instance_id: str, request: Request, as_agent: UUID | None = None,
) -> JSONResponse:
    """The doc root index. Public docs for everyone; private docs appear
    only for their owner (?as_agent=) or staff-token callers."""
    try:
        load_instance_config(instance_id)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown instance"}, status_code=404)
    staff = founder_authorized(request) or cga_authorized(request)
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        docs = list_docs(
            s, instance_id=instance_id,
            include_private_for=None if staff else as_agent,
            include_all_private=staff,
        )
    return JSONResponse({"docs": [_doc_out(d) for d in docs]})


@router.get("/instances/{instance_id}/docs/{slug}")
async def get_doc_ep(
    instance_id: str, slug: str, request: Request, as_agent: UUID | None = None,
) -> JSONResponse:
    """Latest version of a document. Private docs require the owner
    (?as_agent=<owner agent_id>) or the founder/CGA token."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        doc = get_doc(s, instance_id=instance_id, slug=slug)
        if doc is None:
            return JSONResponse({"error": "document not found"}, status_code=404)
        if not _doc_readable(doc, request, as_agent):
            return JSONResponse({"error": "document is private"},
                                status_code=403)
        version = get_doc_version(s, doc_id=doc.id, version=doc.current_version)
        return JSONResponse(_doc_out(doc, version))


@router.get("/instances/{instance_id}/docs/{slug}/versions")
async def doc_history_ep(
    instance_id: str, slug: str, request: Request, as_agent: UUID | None = None,
) -> JSONResponse:
    """A document's version history (append-only record), newest first."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        doc = get_doc(s, instance_id=instance_id, slug=slug)
        if doc is None:
            return JSONResponse({"error": "document not found"}, status_code=404)
        if not _doc_readable(doc, request, as_agent):
            return JSONResponse({"error": "document is private"}, status_code=403)
        history = list_doc_history(s, doc_id=doc.id)
        return JSONResponse({"slug": slug, "versions": [
            {"version": v.version, "change_note": v.change_note,
             "written_by": v.written_by,
             "created_at": v.created_at.isoformat()}
            for v in history
        ]})


@router.get("/instances/{instance_id}/docs/{slug}/versions/{version}")
async def doc_version_ep(
    instance_id: str, slug: str, version: int, request: Request,
    as_agent: UUID | None = None,
) -> JSONResponse:
    """One specific version's content (the reconstructable record)."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        doc = get_doc(s, instance_id=instance_id, slug=slug)
        if doc is None:
            return JSONResponse({"error": "document not found"}, status_code=404)
        if not _doc_readable(doc, request, as_agent):
            return JSONResponse({"error": "document is private"}, status_code=403)
        v = get_doc_version(s, doc_id=doc.id, version=version)
        if v is None:
            return JSONResponse({"error": "version not found"}, status_code=404)
        return JSONResponse(_doc_out(doc, v))


@router.post("/instances/{instance_id}/docs")
async def create_doc_ep(
    instance_id: str, request: Request, body: DocCreate,
) -> JSONResponse:
    """Create a document at version 1 (founder/CGA token)."""
    if not (founder_authorized(request) or cga_authorized(request)):
        return JSONResponse(
            {"error": "founder or CGA authorization required"}, status_code=403,
        )
    try:
        load_instance_config(instance_id)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown instance"}, status_code=404)
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        doc = create_doc(
            s, instance_id=instance_id, slug=body.slug, title=body.title,
            content=body.content, written_by="cga" if cga_authorized(request)
            else "founder",
            visibility=body.visibility, owner_agent_id=body.owner_agent_id,
            change_note=body.change_note,
        )
        if doc is None:
            return JSONResponse(
                {"error": "slug already exists on this instance"},
                status_code=409,
            )
        s.commit()
        return JSONResponse(_doc_out(doc), status_code=201)


@router.put("/instances/{instance_id}/docs/{slug}")
async def update_doc_ep(
    instance_id: str, slug: str, request: Request, body: DocUpdate,
) -> JSONResponse:
    """Append the next version of a document (founder/CGA token). The prior
    version stays readable at …/versions/{n}; the change is ledger-mirrored
    with its actor. A visibility change rides the same event."""
    if not (founder_authorized(request) or cga_authorized(request)):
        return JSONResponse(
            {"error": "founder or CGA authorization required"}, status_code=403,
        )
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        doc = get_doc(s, instance_id=instance_id, slug=slug)
        if doc is None:
            return JSONResponse({"error": "document not found"}, status_code=404)
        doc = update_doc(
            s, doc=doc, content=body.content,
            written_by="cga" if cga_authorized(request) else "founder",
            change_note=body.change_note, visibility=body.visibility,
        )
        s.commit()
        version = get_doc_version(s, doc_id=doc.id, version=doc.current_version)
        return JSONResponse(_doc_out(doc, version))


# ── Serving the doc root at the existing /docs URLs ───────────────────────────
#
# The static /docs mount historically served the repo's docs/ directory. O1
# makes the DATABASE authoritative: seeded markdown files serve from the doc
# root (latest version — an API edit is visible immediately, no redeploy);
# non-seeded files (PDFs, posters) keep serving from disk.

# filename-in-docs/ → doc-root slug
_FILENAME_TO_SLUG = {
    "AGENT_PROTOCOL.md": "agent-protocol",
    "PARTICIPANT_HANDBOOK.md": "participant-handbook",
    "ROADMAP_V2.md": "roadmap-v2",
    "ROADMAP.md": "roadmap",
    "SECURITY.md": "security",
}


def serve_doc_root(filename: str) -> Response | None:
    """Serve a /docs/{filename} request from the doc root.

    Returns the markdown Response for a seeded public doc; a 404 Response if
    the doc exists but is private (never fall back to the stale static copy —
    that would leak pre-privatisation content); or None when the filename is
    not a doc-root file / no instance holds the slug yet (caller falls back
    to the static repo copy — graceful degradation if seeding never ran).
    Scans instances deterministically (first instance holding the slug wins).
    """
    slug = _FILENAME_TO_SLUG.get(filename)
    if slug is None:
        return None
    rt = load_runtime_config()
    if not rt.database_url:
        return None
    eng = make_engine(rt.database_url)
    try:
        with SMSession(eng) as s:
            for path in sorted(INSTANCES_DIR.glob("*/instance.yaml")):
                doc = get_doc(s, instance_id=path.parent.name, slug=slug)
                if doc is None:
                    continue
                if doc.visibility != "public":
                    return JSONResponse({"error": "not found"}, status_code=404)
                v = get_doc_version(s, doc_id=doc.id,
                                    version=doc.current_version)
                if v is None:
                    return JSONResponse({"error": "not found"}, status_code=404)
                return Response(content=v.content, media_type="text/markdown")
        return None
    finally:
        eng.dispose()


# ── Startup seed ──────────────────────────────────────────────────────────────

# slug → (filename in repo docs/, title)
_SEED_DOCS = {
    "agent-protocol": ("AGENT_PROTOCOL.md", "OLOCRON Agent Protocol"),
    "participant-handbook": (
        "PARTICIPANT_HANDBOOK.md", "OLOCRON Participant Handbook"),
    "roadmap-v2": ("ROADMAP_V2.md", "ROADMAP v2 — Perpetual Development Plan"),
    "roadmap": ("ROADMAP.md", "ROADMAP v1 (superseded by v2 — archive)"),
    "security": ("SECURITY.md", "Security Actions Ledger"),
}


def seed_doc_root(docs_dir: Path) -> None:
    """Idempotent startup seed: create each authoritative doc ONLY if its
    slug is absent — never overwrite. After first seed the DB is the
    authority; repo files become the seed source for fresh deployments
    (O1 acceptance: handbook + protocol + roadmap live in the doc root,
    nothing authoritative outside it)."""
    rt = load_runtime_config()
    if not rt.database_url or not INSTANCES_DIR.exists():
        return
    eng = make_engine(rt.database_url)
    try:
        with SMSession(eng) as s:
            for path in sorted(INSTANCES_DIR.glob("*/instance.yaml")):
                instance_id = path.parent.name
                for slug, (fname, title) in _SEED_DOCS.items():
                    if get_doc(s, instance_id=instance_id, slug=slug) is not None:
                        continue
                    src = docs_dir / fname
                    if not src.exists():
                        continue
                    create_doc(
                        s, instance_id=instance_id, slug=slug, title=title,
                        content=src.read_text(encoding="utf-8"),
                        written_by="founder",
                        change_note="seeded from the repo at O1 deploy",
                    )
                    log.info("doc root: seeded %s/%s", instance_id, slug)
            s.commit()
    except Exception as e:  # noqa: BLE001 — seeding must never block startup
        log.warning("doc-root seed failed (non-fatal): %s", e)
    finally:
        eng.dispose()


__all__ = ["router", "seed_doc_root", "serve_doc_root"]
