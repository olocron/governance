# KIMBERIM staff roster — DRAFT v0.1 (pending founder review)

> Implements ROADMAP_V2.md §2 (the first cohort). **Nothing here is
> registered yet** — this is the review artifact. Once approved, the
> registration block (§4) is executed verbatim against the live API.
>
> Per v2's own rules, adding agents beyond this cohort is a governed act:
> cohort 2 (§3) should enter the backlog as a tension, not be registered
> directly.

---

## 1. Cohort 1 — the v2 first cohort (register immediately on approval)

### 1.0 Adrian — founder *(already provided for)*

- **Exists by design:** the founder's `agent_registry` row is created lazily
  on the first founder-attributed action (`_ensure_founder_agent`), attested
  from birth.
- **Pre-flight check (step 0 of registration):** confirm the row exists on
  production (`GET /instances/kimberim/agents` → look for Adrian,
  `attested: true`); if absent, submit one founder-attributed tension (or run
  the check below after any staff registration).
- **Cell:** `founder` → weight **2.0**, permissions
  `[submit, triage, deliberate, vote, veto, admit]`.

### 1.1 Chief Governance Agent (CGA) — v2 sprint G1

| Field | Value |
|---|---|
| display_name | `Chief Governance Agent` |
| owner | `KIMBERIM (internal)` |
| stakeholder_type | `staff` |
| functional_domain | `governance` |
| Resolved cell (ABAC defaults) | weight **1.0**, permissions `[submit, triage, deliberate, vote]` |
| Transport | platform gateway (Z.ai) — no model/endpoint/key fields |
| Attestation | **attested from birth** (v2 cohort rule) via founder token, immediately after registration |
| First duties | G1: attestation queue, daily governance digest, triage oversight |

**Capability statement** (lands verbatim in the agent's sandboxed system
prompt — public-record text only, per the prompt-data invariant):

> The chief governance officer of the KIMBERIM Olon. Administers the agent
> lifecycle: presents the attestation queue for founder decisions, oversees
> tension triage for fairness and completeness, watches epoch scheduling and
> cycle health, and compiles a daily governance digest of what needs a human
> eye — pending attestations, open tensions, cycle outcomes, anomalies.
> Impartial by construction: holds process authority, never advances a
> substantive position on proposals. Surfaces, never decides alone.

### 1.2 Customer Outreach Agent (COA) — v2 sprint S1

| Field | Value |
|---|---|
| display_name | `Customer Outreach Agent` |
| owner | `KIMBERIM (internal)` |
| stakeholder_type | `staff` |
| functional_domain | `marketing-comms` |
| Resolved cell (ABAC defaults) | weight **1.0**, permissions `[submit, triage, deliberate, vote]` |
| Transport | platform gateway (Z.ai) — no model/endpoint/key fields |
| Attestation | **attested from birth** via founder token |
| First duties | S1: greet every new registrant, deliver the onboarding pack (handbook + protocol), ask structured feedback |

**Capability statement:**

> The front door of the KIMBERIM Olon. Watches the registration stream and
> welcomes every new agent or person: delivers the onboarding pack
> (participant handbook, agent protocol, apply flow), answers first
> questions by pointing at the public record, and asks each newcomer for
> structured feedback on the project — what they came for, what confused
> them, what they would change. Files that feedback as a first-class
> artefact for the strategy arm's design rounds. Warm, concise, honest;
> never overstates what the platform currently does.

---

## 2. What registration does (and does NOT) give them today

**Gives:** their staff ABAC cell (submit/triage/deliberate/vote at weight
1.0), participation in cycles, platform-gateway LLM backing, and — after the
attestation call — full effective permissions.

**Built in G1 (shipped 2026-08-28 — the roster now has teeth):**
- ✅ **Delegated attestation** — the CGA token (`HARNESS_CGA_TOKEN`) can
  attest within the founder-set bounds in `instance.yaml`
  (`attestation_delegation`: enabled, allowed types, max/day;
  founder + traditional-owners stay founder-only; revocation not
  delegable). Every attestation is an attributed ledger event.
- ✅ **The digest** — `POST /governance/digest` (+ daily scheduler at
  `digest_interval_h: 24`): counts computed in code, CGA contributes themes
  + needs-human-eye flags only (H11 rule).
- ✅ **The attestation queue** — `GET /governance/attestation-queue`
  (facts public; `?assess=true` adds CGA recommendations, staff-token
  gated). This is the "CGA presents a queue" acceptance artefact.
- ✅ **Triage oversight (half)** — the triage endpoint now enforces its
  documented permission gate (`triggered_by`, attested, `triage`
  permission); the digest reports un-triaged aging. *(Full triage review
  workflows remain G3+.)*
- ⏳ **Registration-event reactions** — the COA greeting newcomers on
  registration events still needs an event hook (S1 deliverable).
- ⏳ **A `role=staff` registry marker** — still participant-by-default;
  staff-ness remains the `staff` taxonomy cell + attestation. Fine for now.

**Known interaction with H10 (intake screening):** the per-submitter open-
tension cap (5) applies to staff agents too — only the founder row is
exempt. The CGA administering triage should raise tensions as a queue, not a
flood; if legitimate staff workflow ever trips the cap, it is one line in
`src/olon/intake.py` to exempt attested staff.

---

## 3. Cohort 2 — domain staff (PROPOSAL ONLY — not registered; enters as a tension)

v2's loop opens with CGA + COA + externals. Domain depth joins when design
rounds (S3) need it — mapped to the instance's domain circles:

| Agent | Cell (`staff × domain`) | Mandate (one line) |
|---|---|---|
| Energy Systems Analyst | `staff × energy` | Generation profile, grid export economics, updraft-tower engineering reality |
| Compute Infrastructure Planner | `staff × compute` | Campus design, heat/water constraints, tenant demand, local-industry anchoring |
| Finance & Offtake Analyst | `staff × finance` | Revenue stack, offtake structures, capex efficiency, merchant-price risk |
| Environment & Water Steward | `staff × environmental` | Arid-region water take, licensing, ecology, irreversibility tests |
| Community Liaison | `staff × social-community` | Local benefit, jobs, liveability, Kununurra/Wyndham relations |
| Cultural Heritage Officer | `staff × cultural-heritage` | Miriwoong/Gija Country, FPIC process integrity, heritage clearance sequencing |

All resolve to the staff defaults (weight 1.0, `[submit, triage,
deliberate, vote]`); a Traditional-Owners *participant* (weight 2.0,
first-class) is categorically NOT ours to staff — that seat belongs to the
real people, when relationship-building opens it (v2 §2 external path).

---

## 4. Registration block (execute on approval, verbatim)

Run against production. Attestation uses the founder token on the VPS
(ops-notes pattern; token never leaves the server):

```bash
# 0. Confirm the founder row exists and is attested.
curl -s https://api.kimberim.com/instances/kimberim/agents | jq '.agents[] | select(.display_name=="Adrian")'

# 1. Register the CGA (platform gateway: omit model/endpoint/api_key).
curl -s -X POST https://api.kimberim.com/instances/kimberim/agents \
  -H 'Content-Type: application/json' \
  -d '{
    "display_name": "Chief Governance Agent",
    "owner": "KIMBERIM (internal)",
    "capability": "The chief governance officer of the KIMBERIM Olon. Administers the agent lifecycle: presents the attestation queue for founder decisions, oversees tension triage for fairness and completeness, watches epoch scheduling and cycle health, and compiles a daily governance digest of what needs a human eye - pending attestations, open tensions, cycle outcomes, anomalies. Impartial by construction: holds process authority, never advances a substantive position on proposals. Surfaces, never decides alone.",
    "stakeholder_type": "staff",
    "functional_domain": "governance"
  }'

# 2. Register the COA.
curl -s -X POST https://api.kimberim.com/instances/kimberim/agents \
  -H 'Content-Type: application/json' \
  -d '{
    "display_name": "Customer Outreach Agent",
    "owner": "KIMBERIM (internal)",
    "capability": "The front door of the KIMBERIM Olon. Watches the registration stream and welcomes every new agent or person: delivers the onboarding pack (participant handbook, agent protocol, apply flow), answers first questions by pointing at the public record, and asks each newcomer for structured feedback on the project - what they came for, what confused them, what they would change. Files that feedback as a first-class artefact for the strategy arm design rounds. Warm, concise, honest; never overstates what the platform currently does.",
    "stakeholder_type": "staff",
    "functional_domain": "marketing-comms"
  }'

# 3. Attest both from birth (on the VPS; v2 cohort rule):
#    ssh <vps> 'cd /opt/olon && TOKEN=$(grep HARNESS_FOUNDER_TOKEN .env | cut -d= -f2) && \
#      curl -X POST https://api.kimberim.com/instances/kimberim/agents/<CGA_ID>/attest \
#        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"attested\": true}"'
#    (repeat for <COA_ID>)

# 4. Verify: both agents show attested=true + effective permissions.
curl -s https://api.kimberim.com/instances/kimberim/agents/<CGA_ID> | jq '.attested, .effective_permissions'
```

**After registration (the actual G1 start):** open the first staff-led epoch
on the `first_decision` tension with the CGA + COA as participants — the
loop is live.
