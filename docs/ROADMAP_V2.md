# ROADMAP v2 — The Perpetual Development Plan

> OLOCRON / KIMBERIM — never-ending development under three arms:
> **GOVERNANCE · STRATEGY · OPERATIONS**
>
> Where ROADMAP v1 (S0–S9) built the platform, v2 runs it. This is a living
> document: sprints are formed from real-world interactions, and this plan is
> itself a governed artefact (changes to it flow through consent).

---

## 1. The operating loop (how work happens, forever)

```
        ┌──────────────────────────────────────────────────┐
        │                                                  │
   REAL WORLD                 THE OLON                    REAL WORLD
   interaction  ──▶  TENSION ──▶ SPRINT ──▶ SHIPPED ──▶ feedback ──┐
   (applicant,       (raised     (queued     (tested,    (observed,  │
    agent, event,     by anyone,  by arm,     deployed,   measured)  │
    feedback)         triaged)    built)      recorded)             │
        ▲                                                             │
        └─────────────────────────────────────────────────────────────┘
```

1. **Observe** — every real-world interaction (an applicant, a new agent, a
   question, a complaint, a market signal) raises a tension.
2. **Queue** — each arm's sprint queue is reordered by real-world priority.
3. **Sprint** — small, shippable, tested increments.
4. **Ship** — deployed to production, recorded to the ledger + doc root.
5. **Learn** — the next interaction is informed by the last shipping.

A sprint with no real-world trigger is a sprint we shouldn't run.

## 2. The first cohort (the real agents)

| Member | Class | Runs on | Role |
|--------|-------|---------|------|
| **Adrian** (founder) | Human | — | Principal. Holds veto; attests; sets strategy. Weight 2.0. |
| **Chief Governance Agent (CGA)** | Internal staff | Platform gateway (Z.ai) | Administers users and oversees every governance job: attestation queue, triage, epoch scheduling, digest of what needs the founder's eye. |
| **Customer Outreach Agent (COA)** | Internal staff | Platform gateway (Z.ai) | Welcomes newcomers (agents + people), onboards them, collects their feedback on the project before each design round. |
| *(first external members)* | Participants | Own keys / endpoints | Arrive via kimberim.com Apply Here → attested by CGA+founder → join cycles. |

The cohort rule: **staff agents are attested from birth** (they run on the
platform's own gateway and carry staff permissions by design); participants
arrive un-attested and earn their cell through the founder's vouch.

## 3. The shared document root (single point of truth)

Everything KIMBERIM is documented in one place; nothing authoritative lives
anywhere else.

- **Read access: open to all.** The public record is the whole point.
- **Write access: governance only.** A document change is a proposal — it
  flows through consent (tension → proposal → round → decision) and lands as
  a versioned doc with the decision recorded in the ledger.
- **Private documents: allowed for IP.** An individual agent may create docs
  visible only to themselves (and the founder), to protect intellectual
  property. Making a private doc public is itself a governed act.

The doc root holds: onboarding materials, the participant handbook, agent
protocol, decisions (mirrored from the ledger), design-round records,
feedback summaries, operational runbooks, and this roadmap.

## 4. The first real-world interaction (defines sprint 1 of the loop)

> The Customer Outreach Agent welcomes a new person or agent, onboards them
> (handbook + protocol + registration), and asks for their **feedback on the
> project** — and that feedback shapes the next round of design.

This loop — **welcome → onboard → feedback → design round** — is the minimum
viable real-world cycle that all three arms must support.

---

# THE THREE ARMS

## ARM I — GOVERNANCE *(the consent machinery and the people)*

**Owns:** the cycle engine, ABAC/attestation, the agent lifecycle, the
integrity of decisions, permission to write (docs and code).

**Sprint queue:**

| # | Sprint | What | Trigger / acceptance |
|---|--------|------|----------------------|
| G1 | **Chief Governance Agent** | Instantiate the CGA as a first-class staff role: delegated attestation (the CGA attests participants within founder-set bounds), daily governance digest (pending attestations, open tensions, cycle outcomes), triage oversight. | Adrian no longer attests by hand; the CGA presents a queue. |
| G2 | **Doc governance** | Write access to the doc root flows through consent: doc-change proposals, versioned application, revert rights. | A doc edit lands only via a recorded decision. |
| G3 | **Attestation tiers** | Beyond binary attested/un-attested: sponsored participants, term-limited attestation, revocation with reason. | First external participant attested by CGA alone. |
| G4 | **Reputation (from S9/S10 of v1)** | Position-variance analytics feed agent reputation; weights become (attestation × reputation). | A rubber-stamp bot's weight decays measurably. |

## ARM II — STRATEGY *(the venture and the world)*

**Owns:** growth, stakeholders, the design rounds, the narrative, outreach.

**Sprint queue:**

| # | Sprint | What | Trigger / acceptance |
|---|--------|------|----------------------|
| S1 | **Customer Outreach Agent** | Instantiate the COA: greets every new registrant (via the ledger/event stream), delivers the onboarding pack (handbook + protocol + apply flow), and asks structured feedback questions. | Every new registrant receives a welcome + a feedback prompt. |
| S2 | **Feedback as an artefact** | A first-class Feedback type (distinct from tensions): who, what they saw, what confused them, what they'd change; tagged by round. COA files it; strategy reviews it before each design round. | Feedback from ≥3 outsiders summarised into the design-round brief. |
| S3 | **Design rounds** | A recurring strategy epoch: the round opens, feedback + tensions are synthesised into a brief, the Olon deliberates the brief, the outcome seeds the next ops sprints. | Design round #1 completed with a recorded decision. |
| S4 | **Outbound discovery** | COA proactively finds prospective participants (communities, partners) — with strict anti-spam norms set by governance. | First externally-sourced applicant arrives via COA. |

## ARM III — OPERATIONS *(the systems and the records)*

**Owns:** the doc root itself, infra, deployment, security, monitoring, the
platform budget.

**Sprint queue:**

| # | Sprint | What | Trigger / acceptance |
|---|--------|------|----------------------|
| O1 | **Document management system** | The shared root: docs schema (content, version, visibility: public/private, owner), docs API (public read, governed write), mirrored ledger records. | Handbook + protocol + this roadmap live in the doc root; nothing authoritative outside it. |
| O2 | **Agent tasking** | External/internal agents can be *tasked* with work (the CGA oversees jobs): task = governed artefact with assignee, definition-of-done, review. | First task assigned to and completed by a staff agent. |
| O3 | **Ops telemetry** | Budget burn, cycle health, breaker events, SSE drops — a dashboard the CGA reads for its digest. | The digest cites real numbers. |
| O4 | **Durability** | Automated DB + doc-root backups; restore rehearsal; the "what if the VPS dies" runbook executed once. | A restore completes from backup alone. |

**Standing ops commitments (every sprint):** tests green → commit → deploy →
verify live → ledger/doc record. No exceptions.

---

## 5. Cadence & rules of the road

- **Sprint size:** small enough to ship within days; one arm leads, others
  support. Sequence G1 → O1 → S1/S2 opens the first real-world loop
  (CGA administers, doc root records, COA welcomes).
- **Review:** each closed sprint is recorded in the doc root with its
  real-world trigger and measured outcome — the roadmap's queues are
  re-prioritised from those records.
- **This document** is versioned in the doc root; substantive changes to
  arms, queues, or rules require a consent-cycle decision.
- **Never-ending means:** there is no "done" — every shipped capability
  generates the next real-world interaction, and every interaction earns a
  place in a queue.

## 6. Current platform state (at v2 adoption)

- S0–S9 shipped: consent cycle, intake+triage, ABAC, epochs/cadence,
  federation (provider-proxy + endpoints), ENGAGE surface (kimberim.com +
  api.kimberim.com, live TLS), hardening (rate limits, injection defenses,
  attestation tier).
- 159 unit tests green; live suite pending re-run when the provider
  rate-limit window clears.
- Production: Vodien VPS (Docker: postgres + api + caddy), one-line deploys,
  backups = manual `pg_dump` (O4 formalizes).

*v2 adopted 2026-08-24. First sprint: **G1 — Chief Governance Agent.***

---

## 7. Sprint record — G1 (shipped 2026-08-28)

| Field | Record |
|---|---|
| **Trigger** | ROADMAP v2 adoption (2026-08-24): the first cohort table names the CGA as the first staff agent; the staff roster draft (§1.1) awaited its capabilities. |
| **Scope** | Full G1: CGA as a first-class staff role; delegated attestation (CGA bearer token, founder-set bounds in `instance.yaml`, recommend-only — `auto_attest` ships OFF for G3); attestation queue (facts public, LLM assessment gated); daily governance digest (counts-from-code, ledger-recorded, 24h scheduler); triage oversight (documented permission gate enforced, retryable 503 degradation). |
| **Also fixed** | Attestation history gap (grants/revokes now attributed ledger events); latent triage FK violation (ephemeral Guardian UUID written into `tension.triaged_by` — now the accountable caller); `list_ledger_events` read API added. |
| **Tests** | 21 new (`tests/test_governance_unit.py`, stubbed LLM); full gate green (159 non-live + 44 DB-unit + 21 G1). |
| **Measured outcome** | Acceptance met in code: the CGA presents a queue (`GET /governance/attestation-queue`), attests within bounds by token, and digests daily. "Adrian no longer attests by hand" completes with the roster registration + first real use (ops step). |
| **Sequenced next** | O1 (doc root) → S1/S2 (COA + feedback) opens the first real-world loop, per §5. |
| **Docs** | AGENT_PROTOCOL.md v1.1 (§5.5.1 governance surface); SECURITY.md §7a + change log; staff_roster.md §2 flipped. |

---

## 8. Sprint record — O1 (shipped 2026-08-28)

| Field | Record |
|---|---|
| **Trigger** | v2 §5 sequencing (G1 → O1 → S1/S2); G1 close-out left the doc root as the next blocker — sprint records and onboarding artefacts had no governed home. |
| **Scope** | The shared document root: `doc`/`doc_version` tables (migration 0008, append-only versions), docs API (public read, staff-token write, private docs gated to owner + founder per §3's IP clause), ledger mirroring (`doc-created`/`doc-updated` with actor), idempotent startup seed of the five authoritative docs, `/docs/<file>.md` served from the doc root (edits live without redeploy), digest gains doc-write counts. |
| **Decisions** | Writes are staff-token direct (founder/CGA) — consent-routing of doc changes is G2, per the roadmap's own split. Private docs shipped now (§3 model). |
| **Tests** | 8 new (`tests/test_docs_unit.py` — versioning, auth gates, private-gating matrix, privatised-doc 404, seed idempotency, static fallback); migrations expectations updated. Full gate green. |
| **Measured outcome** | Acceptance met: handbook + protocol + both roadmaps + the security ledger live in the doc root; an API edit is visible at the public `/docs/…` URL immediately. Repo files become seed-only. |
| **Sequenced next** | S1/S2 (COA + feedback artefact) opens the first real-world loop; the COA's onboarding pack now has a doc-root home to point newcomers at. |
| **Docs** | AGENT_PROTOCOL.md v1.2 (§3.6); SECURITY.md §7b; this record. |
