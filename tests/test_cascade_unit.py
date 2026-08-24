"""H11 anti-cascade tests: statistical digest, blind rounds, non-normative
framing.

The threat: the H8 digest feeds later rounds, so a first-round Sybil consent
that produces "most participants consent" prose anchors every later round
toward consent (herding — manipulable at the margin by whoever moves first).

The defenses asserted here:
  1. Digest COUNTS are computed in code from enum-validated positions — an
     LLM never authors them (a Summarizer contributes themes only).
  2. Round-1 positions are BLIND: the position prompt contains the proposal
     and nothing peer-derived (no positions, no digest, no counts).
  3. The digest reaches the Mediator phrased as a statistical summary with an
     explicit not-a-normative-signal caveat — never as consensus language.
"""

from __future__ import annotations

from olon.agents import StubAgent
from olon.config import GovernanceConfig
from olon.cycle import CycleRun, run_cycle
from olon.cycle.nodes import statistical_digest
from olon.schema import AgentRole, Tension

INSTANCE = "kimberim"

_PROP = (
    '{"title":"Cap compute at 30%","context":"1GW campus",'
    '"change":"max 30% to compute","expected_impact":"protects revenue",'
    '"safe_to_try_rationale":"reversible quarterly; no role regressed"}'
)


def _arch() -> StubAgent:
    return StubAgent(_PROP, role=AgentRole.PROPOSAL_ARCHITECT, display_name="arch")


def _da() -> StubAgent:
    return StubAgent('{"position": "consent"}', role=AgentRole.DEVILS_ADVOCATE,
                     display_name="da")


def _participant(position: str, *, name: str, weight: float = 1.0) -> StubAgent:
    return StubAgent(
        f'{{"position": "{position}"}}',
        role=AgentRole.PARTICIPANT, display_name=name, weight=weight,
    )


def _run(arch, da, *, participants=None, mediator=None, summarizer=None):
    tension = Tension(instance_id=INSTANCE, raised_by=arch.ref, title="t", description="d")
    events: list[tuple[str, dict]] = []
    run = CycleRun(
        instance_id=INSTANCE, tension=tension, participants=[arch.ref],
        governance=GovernanceConfig(),
        proposal_architect=arch, devils_advocate=da,
        participant_agents=participants or [],
        integrative_mediator=mediator, summarizer=summarizer,
        ledger_sink=lambda et, p: events.append((et, p)),
    )
    return run, events


# ── 1. The digest's counts are computed in code ──────────────────────────────


def test_statistical_digest_counts_and_weights():
    positions = [
        {"agent_id": "1", "position": "consent", "weight": 2.0},
        {"agent_id": "2", "position": "consent", "weight": 1.0},
        {"agent_id": "3", "position": "objection", "weight": 1.5},
        {"agent_id": "4", "position": "abstain", "weight": 1.0},
        {"agent_id": "5", "position": "consent", "weight": 0.5},
    ]
    d = statistical_digest(positions)
    assert d["consent_count"] == 3
    assert d["objection_count"] == 1
    assert d["abstain_count"] == 1
    assert d["weighted_consent"] == 3.5
    assert d["weighted_objection"] == 1.5
    assert "not" in d["framing"], "the framing contract must disclaim normative use"


def test_digest_ignores_llm_authored_counts():
    """A Summarizer (or an attacker influencing it) cannot author the counts:
    even when the stub returns WRONG counts, the digest carries the
    code-computed ones. The round here never consents (the objector is
    static), so the cycle escalates — irrelevant to the assertion, which is
    about what the digest contains."""
    lying_summarizer = StubAgent(
        '{"consent_count": 999, "objection_count": 0, "themes": ["revenue-risk"]}',
        role=AgentRole.SUMMARIZER, display_name="sum",
    )
    run, events = _run(
        _arch(), _da(),
        participants=[
            _participant("consent", name="p1"),
            _participant("objection", name="p2"),
            _participant("consent", name="p3"),
        ],
        summarizer=lying_summarizer,
    )
    final = run_cycle(run)
    digest = final["digest"]
    assert digest is not None
    # Code-computed truth, not the LLM's claim.
    assert digest["consent_count"] == 3  # p1 + p3 + DA
    assert digest["objection_count"] == 1
    # The Summarizer's themes survive; its counts never do.
    assert digest["themes"] == ["revenue-risk"]
    assert any(et == "digest" for et, _ in events)


def test_digest_exists_without_summarizer():
    """>2 positions produce a counts-only digest even with no Summarizer wired
    — the Mediator gets the consensus shape regardless (the digest is now a
    code product; the Summarizer is an optional theme-extractor)."""
    run, events = _run(
        _arch(), _da(),
        participants=[_participant("consent", name=f"p{i}") for i in range(3)],
    )
    final = run_cycle(run)
    assert final["digest"] is not None
    assert final["digest"]["consent_count"] == 4  # 3 participants + DA
    assert "themes" not in final["digest"]
    assert any(et == "digest" for et, _ in events)


# ── 2. Round-1 positions are blind ────────────────────────────────────────────


def test_position_prompt_is_blind_to_peers():
    """The position prompt each agent receives contains the proposal and NO
    peer-derived content: no digest, no counts, no other agent's stated
    position. Whoever moves first cannot anchor the round.

    Markers: the instruction itself enumerates the position enum exactly once
    ('"position": "consent" | ...'), so a second occurrence — or any digest
    key — means peer output leaked into the ask. (agent_id/display_name/
    weight appear legitimately inside the proposal's drafted_by ref, so they
    are not usable markers.)"""
    seen_prompts: list[str] = []

    def _capture(prompt: str, _ctx: str) -> str:
        seen_prompts.append(prompt)
        return '{"position": "consent"}'

    participants = [
        StubAgent(_capture, role=AgentRole.PARTICIPANT, display_name=f"p{i}")
        for i in range(4)
    ]
    run, _ = _run(_arch(), _da(), participants=participants)
    run_cycle(run)
    assert len(seen_prompts) == 4, "every participant should have been asked once"
    for p in seen_prompts:
        assert "Cap compute at 30%" in p, "the proposal itself must be present"
        assert "Round digest" not in p
        assert "consent_count" not in p
        assert "weighted_consent" not in p
        # Exactly ONE occurrence of the "position" key (the instruction's
        # enum) — a peer's stated position serialized into the prompt would
        # add another. (The instruction writes '"position": "consent" | ...',
        # so per-value counts are not usable markers.)
        assert p.count('"position"') == 1


def test_summarizer_contract_is_themes_only():
    """The Summarizer's standing contract: themes only, no counts, no
    normative framing (the anti-herding output contract)."""
    from olon.agents.roles import Summarizer

    sp = Summarizer.system_prompt.lower()
    assert "themes" in sp
    assert "never state counts" in sp
    assert "forbidden" in sp
    # It must not ask for the count keys it used to produce (H11 redesign).
    assert "consent_count" not in sp


# ── 3. The Mediator sees a statistical summary, not a normative signal ────────


def test_mediator_digest_phrasing_is_statistical():
    """H8 kept the digest wired into the Mediator's prompt; H11 rephrases it:
    labelled a STATISTICAL SUMMARY with an explicit not-an-endorsement caveat,
    stating that majority size is not the correct answer."""
    seen_prompts: list[str] = []

    def _mediator(prompt: str, _ctx: str) -> str:
        seen_prompts.append(prompt)
        return ('{"title":"amended","context":"c","change":"ch2",'
                '"expected_impact":"i","safe_to_try_rationale":"s2"}')

    summarizer = StubAgent(
        '{"themes": ["revenue-risk"]}',
        role=AgentRole.SUMMARIZER, display_name="sum",
    )
    mediator = StubAgent(_mediator, role=AgentRole.INTEGRATIVE_MEDIATOR,
                         display_name="med")
    # One objector (forces the amend path), then consents post-amendment.
    q = ['{"position": "objection", "criterion": "not-safe-to-try", "reason": "revenue"}',
         '{"position": "consent"}']
    obj = StubAgent(lambda _p, _c: q.pop(0), role=AgentRole.PARTICIPANT,
                    display_name="obj")

    run, events = _run(
        _arch(), _da(),
        participants=[obj, _participant("consent", name="c1"),
                      _participant("consent", name="c2")],
        mediator=mediator, summarizer=summarizer,
    )
    final = run_cycle(run)
    assert final["outcome"] == "adopted"

    amend_prompt = next(p for p in seen_prompts if "Round digest" in p)
    low = amend_prompt.lower()
    assert "statistical summary" in low
    assert "not an endorsement or normative signal" in low
    assert "majority size is not the correct answer" in low
    # The counts arrive as code-computed JSON facts.
    assert '"consent_count": 3' in amend_prompt  # c1 + c2 + DA
    assert '"objection_count": 1' in amend_prompt
    # And the theme still flows through (H8 regression guard).
    assert "revenue-risk" in amend_prompt
