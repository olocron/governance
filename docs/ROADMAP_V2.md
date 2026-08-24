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
