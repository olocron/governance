"""The G1 governance surface — the Chief Governance Agent's endpoints.

ROADMAP_V2 sprint G1: the CGA is the first staff agent of the first cohort.
This module gives its duties an HTTP shape:

  GET  /instances/{id}/governance/attestation-queue[?assess=true]
       The queue the CGA presents: every un-attested agent with deterministic
       facts; with ?assess=true (CGA/founder token) each entry also carries
       the CGA's structured recommendation. LLM failure degrades to
       "recommend: review" per entry — the queue itself is always facts-only.
  POST /instances/{id}/governance/digest   (CGA/founder token)
       Compile the governance digest now: counts computed in code (the H11
       rule — the LLM contributes themes + flags, never counts), persisted as
       a `governance-digest` ledger event.
  GET  /instances/{id}/governance/digest/latest
       The most recent digest (public record — it is in the ledger anyway).

Anything that burns LLM budget (assess, digest build) requires the CGA or
founder token; the facts-only reads are public. The scheduler (scheduler.py)
calls build_governance_digest() directly on cadence — no token in-process.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session as SMSession

from olon.agents import ChiefGovernanceAgent
from olon.config import InstanceConfig, load_instance_config, load_runtime_config
from olon.security import MAX_REASON_LEN, clean, sandbox
from olon.store import (
    append_ledger_event,
    list_agents,
    list_backlog,
    list_ledger_events,
    make_engine,
)
from olon.utils import extract_json

log = logging.getLogger(__name__)

router = APIRouter()

# The fixed framing line every digest carries (H11: counts are observed
# facts, not an endorsement — mirrors cycle.nodes.statistical_digest).
DIGEST_FRAMING = (
    "Counts are observed facts, not an endorsement; themes are the Chief "
    "Governance Agent's neutral reading of those facts."
)

# Caps so a large instance can't blow up the digest prompt/payload.
_MAX_QUEUE_IN_DIGEST = 10
_MAX_TENSION_TITLES_IN_PROMPT = 20
_MAX_THEMES = 10
_MAX_FLAGS = 10
_MAX_REASONS = 5
_UNTRIAGED_AGE_FLAG_H = 24.0


# ── Auth helpers ──────────────────────────────────────────────────────────────


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def founder_authorized(request: Request) -> bool:
    """True if the request carries the founder bearer token (HARNESS_FOUNDER_TOKEN)."""
    rt = load_runtime_config()
    return bool(rt.founder_token) and _bearer(request) == rt.founder_token


def cga_authorized(request: Request) -> bool:
    """True if the request carries the CGA bearer token (HARNESS_CGA_TOKEN).

    Distinct from the founder token on purpose: the CGA's powers are bounded
    (see delegation_bounds_error) and must never equal the founder's.
    """
    rt = load_runtime_config()
    return bool(rt.cga_token) and _bearer(request) == rt.cga_token


def staff_authorized(request: Request) -> bool:
    """CGA or founder — the two tokens allowed to spend LLM budget here."""
    return cga_authorized(request) or founder_authorized(request)


# ── Delegated attestation bounds (G1) ────────────────────────────────────────


def _payload(row) -> dict:
    try:
        return json.loads(row.payload)
    except (json.JSONDecodeError, TypeError):
        return {}


def cga_attestations_past_24h(s: SMSession, *, instance_id: str) -> int:
    """Count attestations the CGA executed in the rolling 24h window.

    Computed from the ledger (the public record), not a counter table —
    the ledger is the single source of truth for who attested whom.
    """
    since = datetime.now(UTC) - timedelta(hours=24)
    rows = list_ledger_events(
        s, instance_id=instance_id, event_type="agent-attested", since=since,
    )
    return sum(1 for r in rows if _payload(r).get("actor") == "cga")


def delegation_bounds_error(
    s: SMSession, ic: InstanceConfig, target_row, *, instance_id: str,
) -> JSONResponse | None:
    """G1: bounds the CGA token holder must stay within to attest.

    Returns the error response to send, or None if within bounds. The founder
    cell is excluded unconditionally (staff never attests the principal);
    revocation is NOT delegable at all — only grants flow through the CGA.
    """
    deleg = ic.governance.attestation_delegation
    if deleg is None or not deleg.enabled:
        return JSONResponse(
            {"error": "attestation delegation is disabled for this instance"},
            status_code=403,
        )
    st = target_row.stakeholder_type
    if st is None or st not in deleg.allowed_stakeholder_types:
        return JSONResponse({
            "error": "stakeholder type outside the CGA's delegated bounds",
            "stakeholder_type": st,
            "allowed": deleg.allowed_stakeholder_types,
            "escalate_to": "founder",
        }, status_code=403)
    used = cga_attestations_past_24h(s, instance_id=instance_id)
    if used >= deleg.max_per_day:
        return JSONResponse({
            "error": "CGA daily attestation cap reached",
            "max_per_day": deleg.max_per_day,
            "used_past_24h": used,
            "escalate_to": "founder",
        }, status_code=403)
    return None


# ── Attestation queue ─────────────────────────────────────────────────────────


def _normalize_recommend(payload: dict) -> dict:
    """Hand-rolled normalization of the CGA's assessment (house style: no
    pydantic response models for agent output). Unknown → the safe default
    'review' — the CGA surfaces; a human decides."""
    rec = payload.get("recommend")
    recommend = rec if rec in ("attest", "review", "decline") else "review"
    raw_reasons = payload.get("reasons")
    reasons = []
    if isinstance(raw_reasons, list):
        for r in raw_reasons[:_MAX_REASONS]:
            if isinstance(r, str) and r.strip():
                reasons.append(clean(r)[:MAX_REASON_LEN])
    return {"recommend": recommend, "reasons": reasons}


def build_attestation_queue(
    s: SMSession, *, ic: InstanceConfig, assess: bool = False,
) -> dict:
    """The CGA's queue: un-attested agents, oldest first, facts only —
    plus (assess=True) one CGA recommendation per agent.

    `within_bounds` is computed in CODE from the delegation config (the LLM
    never decides what it is allowed to do); the LLM contributes only the
    advisory recommend/reasons. LLM failure degrades the entry to
    recommend='review' with an explicit note — the queue never 500s.
    """
    instance_id = ic.instance_id
    rows = [a for a in list_agents(s, instance_id=instance_id) if not a.attested]
    rows.sort(key=lambda a: a.created_at)
    deleg = ic.governance.attestation_delegation
    allowed = list(deleg.allowed_stakeholder_types) if deleg else []

    cga = ChiefGovernanceAgent(instance_id=instance_id) if assess else None
    queue: list[dict] = []
    for row in rows:
        entry: dict = {
            "agent_id": str(row.agent_id),
            "display_name": row.display_name,
            "owner": row.owner,
            "capability": row.capability,
            "stakeholder_type": row.stakeholder_type,
            "functional_domain": row.functional_domain,
            "registered_at": row.created_at.isoformat(),
            # Bounds are deterministic, never LLM-judged:
            "within_bounds": bool(
                deleg is not None and deleg.enabled
                and row.stakeholder_type in deleg.allowed_stakeholder_types
            ),
        }
        if assess:
            prompt = (
                "Assess this registration for attestation. The counts and "
                "bounds are computed elsewhere — you only advise. Respond "
                "ONLY as JSON with keys: recommend ('attest' | 'review' | "
                "'decline'), reasons (list of str, no counts).\n"
                f"Display name:\n{sandbox('display name', row.display_name, max_len=200)}\n"
                f"Owner:\n{sandbox('owner', row.owner, max_len=200)}\n"
                f"Capability:\n{sandbox('capability', row.capability, max_len=2000)}\n"
                f"Stakeholder type: {row.stakeholder_type}\n"
                f"Functional domain: {row.functional_domain}\n"
                f"Registered at: {row.created_at.isoformat()}\n"
                f"Stakeholder types within the CGA's bounds: {json.dumps(allowed)}\n"
                "Recommend 'attest' only when the registration is coherent, "
                "on-domain for the Olon, and its claimed cell is plausible. "
                "Recommend 'review' when a human should look first. Recommend "
                "'decline' only on clear abuse signals."
            )
            try:
                cga_text = cga.respond(prompt, max_tokens=400, temperature=0.2)
                entry["cga_assessment"] = _normalize_recommend(extract_json(cga_text))
            except Exception as e:  # noqa: BLE001 — degrade, never 500
                log.warning("CGA queue assessment failed for %s: %s", row.agent_id, e)
                entry["cga_assessment"] = {
                    "recommend": "review",
                    "reasons": ["assessment unavailable (agent call failed)"],
                }
        queue.append(entry)

    out: dict = {
        "instance_id": instance_id,
        "pending_count": len(queue),
        "delegation": (
            {
                "enabled": deleg.enabled,
                "max_per_day": deleg.max_per_day,
                "used_past_24h": cga_attestations_past_24h(s, instance_id=instance_id),
                "allowed_stakeholder_types": allowed,
                "auto_attest": deleg.auto_attest,
            }
            if deleg is not None
            else {"enabled": False}
        ),
        "queue": queue,
    }
    return out


# ── Governance digest ─────────────────────────────────────────────────────────


def _normalize_themes(payload: dict) -> dict:
    """Normalize the CGA's digest contribution: themes + flags, strings only.
    Bad output → empty contribution (the digest stands on its code-computed
    facts)."""
    def _str_list(v) -> list[str]:
        out: list[str] = []
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.strip():
                    out.append(clean(item)[:MAX_REASON_LEN])
        return out

    return {
        "themes": _str_list(payload.get("themes"))[:_MAX_THEMES],
        "needs_human_eye": _str_list(payload.get("needs_human_eye"))[:_MAX_FLAGS],
    }


def build_governance_digest(
    s: SMSession, *, ic: InstanceConfig, with_themes: bool = True,
) -> dict:
    """Compile the governance digest: counts computed in code (H11 — the
    LLM contributes themes + flags, never counts), then append a
    `governance-digest` ledger event (the caller commits).

    Window: since the previous governance-digest event (all time if none).
    LLM failure degrades to themes=[] with an explicit note — the digest is
    always recorded; an unavailable theme call must not lose the day's facts.
    """
    instance_id = ic.instance_id
    now = datetime.now(UTC)

    prior = list_ledger_events(
        s, instance_id=instance_id, event_type="governance-digest", limit=1,
    )
    window_start = prior[0].created_at if prior else None

    def _count(event_type: str) -> int:
        return len(list_ledger_events(
            s, instance_id=instance_id, event_type=event_type, since=window_start,
        ))

    def _decision_outcomes() -> dict[str, int]:
        rows = list_ledger_events(
            s, instance_id=instance_id, event_type="decision-recorded",
            since=window_start,
        )
        outcomes = {"adopted": 0, "rejected": 0, "escalated": 0}
        for r in rows:
            o = _payload(r).get("outcome")
            if o in outcomes:
                outcomes[o] += 1
        return outcomes

    # ── Attestation facts (registry + ledger) ──
    pending = [a for a in list_agents(s, instance_id=instance_id) if not a.attested]
    pending.sort(key=lambda a: a.created_at)
    # Ages are computed in SQL: row timestamps are stored tz-naive in the
    # session timezone, so Python-side UTC arithmetic would be off by the
    # server's UTC offset.
    oldest_h = 0.0
    if pending:
        got = s.execute(text(
            "SELECT EXTRACT(EPOCH FROM (now() - MIN(created_at))) / 3600.0 "
            "FROM agent_registry WHERE instance_id = :i AND attested = FALSE"
        ), {"i": instance_id}).scalar()
        oldest_h = round(float(got), 1) if got else 0.0
    granted = _count("agent-attested")
    revoked = _count("agent-attestation-revoked")

    # ── Tension facts (backlog states + triage oversight aging) ──
    by_status: dict[str, int] = {}
    for t in list_backlog(s, instance_id=instance_id):
        by_status[t.status] = by_status.get(t.status, 0) + 1
    untriaged_old = int(s.execute(text(
        "SELECT COUNT(*) FROM tension "
        "WHERE instance_id = :i AND status = 'open' "
        "AND created_at < now() - (:h * interval '1 hour')"
    ), {"i": instance_id, "h": _UNTRIAGED_AGE_FLAG_H}).scalar())
    aged = s.execute(text(
        "SELECT title FROM tension "
        "WHERE instance_id = :i AND status = 'open' "
        "AND created_at < now() - (:h * interval '1 hour') "
        "ORDER BY created_at ASC LIMIT :n"
    ), {"i": instance_id, "h": _UNTRIAGED_AGE_FLAG_H,
        "n": _MAX_TENSION_TITLES_IN_PROMPT}).all()
    tension_titles = [row[0] for row in aged]

    # ── Cycle facts (ledger events in the window) ──
    outcomes = _decision_outcomes()
    cycles = {
        "decisions_recorded": sum(outcomes.values()),
        "outcomes": outcomes,
        "epochs_opened": _count("epoch-opened"),
        "epoch_skips": _count("epoch-skipped"),
        "escalations": _count("escalation"),
        "founder_vetoes": _count("founder-veto"),
    }

    # ── Deterministic flags (code-computed; the LLM may ADD, not replace) ──
    flags: list[str] = []
    if pending:
        flags.append(
            f"agents awaiting attestation (oldest has waited {oldest_h}h)"
        )
    if untriaged_old:
        flags.append("tensions un-triaged beyond the triage window")
    if cycles["escalations"]:
        flags.append("cycle escalations in the window")
    if cycles["founder_vetoes"]:
        flags.append("founder vetoes in the window")

    facts = {
        "window": {
            "since": window_start.isoformat() if window_start else None,
            "generated_at": now.isoformat(),
        },
        "attestations": {
            "pending_count": len(pending),
            "oldest_pending_wait_h": oldest_h,
            "granted": granted,
            "revoked": revoked,
        },
        "tensions": {"by_status": by_status, "untriaged_old": untriaged_old},
        "cycles": cycles,
    }

    # ── The CGA's contribution: themes + flags, never counts ──
    themes: list[str] = []
    llm_flags: list[str] = []
    cga_note = "not requested"
    if with_themes:
        titles_block = "\n".join(
            sandbox("tension title", t, max_len=200) for t in tension_titles
        ) or "(no aging un-triaged tensions)"
        prompt = (
            "Compile the governance digest for this Olon. The observed facts "
            "below are computed in code and are authoritative — you contribute "
            "themes and flags ONLY. Never state counts, proportions, or "
            "majority sizes; never say what the collective 'supports' or "
            "'should' conclude.\n"
            f"Observed facts (code-computed): {json.dumps(facts)}\n"
            f"Titles of aging un-triaged tensions:\n{titles_block}\n"
            "Respond ONLY as JSON with keys: themes (list of str), "
            "needs_human_eye (list of str, no numbers)."
        )
        cga = ChiefGovernanceAgent(instance_id=instance_id)
        try:
            cga_text = cga.respond(prompt, max_tokens=600, temperature=0.2)
            contrib = _normalize_themes(extract_json(cga_text))
            themes = contrib["themes"]
            llm_flags = contrib["needs_human_eye"]
            cga_note = "ok"
        except Exception as e:  # noqa: BLE001 — degrade, never fail the digest
            log.warning("CGA digest themes failed for %s: %s", instance_id, e)
            cga_note = "unavailable (agent call failed); facts stand without themes"

    digest = {
        "instance_id": instance_id,
        "facts": facts,
        "themes": themes,
        "needs_human_eye": flags + [f for f in llm_flags if f not in flags],
        "cga": cga_note,
        "framing": DIGEST_FRAMING,
    }

    append_ledger_event(
        s, instance_id=instance_id, event_type="governance-digest",
        payload=digest,
    )
    return digest


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/instances/{instance_id}/governance/attestation-queue")
async def attestation_queue(
    instance_id: str, request: Request, assess: bool = False,
) -> JSONResponse:
    """The CGA's attestation queue: un-attested agents + deterministic facts.

    ?assess=true adds the CGA's structured recommendation per entry (LLM
    call — requires the CGA or founder token; the facts-only queue is public).
    """
    if assess and not staff_authorized(request):
        return JSONResponse(
            {"error": "CGA or founder authorization required for assessment"},
            status_code=403,
        )
    try:
        ic = load_instance_config(instance_id)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown instance"}, status_code=404)
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        queue = build_attestation_queue(s, ic=ic, assess=assess)
    return JSONResponse(queue)


@router.post("/instances/{instance_id}/governance/digest")
async def build_digest(instance_id: str, request: Request) -> JSONResponse:
    """Compile + persist the governance digest now (CGA or founder token —
    the call spends LLM budget; the scheduler runs it on cadence without a
    token)."""
    if not staff_authorized(request):
        return JSONResponse(
            {"error": "CGA or founder authorization required"}, status_code=403,
        )
    try:
        ic = load_instance_config(instance_id)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown instance"}, status_code=404)
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        digest = build_governance_digest(s, ic=ic)
        s.commit()
    return JSONResponse(digest)


@router.get("/instances/{instance_id}/governance/digest/latest")
async def latest_digest(instance_id: str) -> JSONResponse:
    """The most recent persisted governance digest (public record)."""
    rt = load_runtime_config()
    eng = make_engine(rt.database_url)
    with SMSession(eng) as s:
        rows = list_ledger_events(
            s, instance_id=instance_id, event_type="governance-digest", limit=1,
        )
        if not rows:
            return JSONResponse({"error": "no digest recorded yet"}, status_code=404)
        try:
            payload = json.loads(rows[0].payload)
        except (json.JSONDecodeError, TypeError):
            return JSONResponse({"error": "digest payload unreadable"}, status_code=500)
        return JSONResponse({
            "sequence": rows[0].sequence,
            "created_at": rows[0].created_at.isoformat(),
            "digest": payload,
        })


__all__ = [
    "build_attestation_queue",
    "build_governance_digest",
    "cga_attestations_past_24h",
    "delegation_bounds_error",
    "router",
]
