"""Backlog-flooding defense (H10, docs/SECURITY.md): intake screening.

Triage is a soft gate by design — it flags duplicates, never blocks. That
leaves the backlog floodable: an attacker (or just an over-eager agent) can
bury real tensions under a hundred near-duplicates, and next_tension serves
the flood because every parked-eligible row sits in the same priority queue.

The policy here PARKS, never deletes — the consent-governance rule that the
record is complete and public applies to spam too:

  1. DEDUP — a near-identical tension from the SAME submitter is parked as a
     duplicate of the original (same submitter only: two different agents
     raising the same issue is convergence signal, not spam). The threshold is
     a similarity ratio over normalized text (punctuation/case/whitespace
     flattened), so trivial rewordings don't dodge it.
  2. CAP — a submitter with MAX_OPEN_PER_SUBMITTER tensions already in the
     queue (open/triaged) has further submissions parked over-cap. The
     founder's rows are exempt: the founder sponsors anonymous submissions and
     seeds, and flooding-by-founder is not the threat model.

Parked tensions keep their `tension-raised` ledger event (with the park
reason), remain readable at GET /tensions, and can still be deliberated by
explicit tension_id — only queue eligibility (next_tension) changes. That
keeps the gate reversible and visible, consistent with triage's philosophy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from olon.store import TensionRow

# ── Tunables ──────────────────────────────────────────────────────────────────

# Similarity ratio (0..1) over normalized title+description at which a
# same-submitter tension counts as a near-duplicate. 0.85 catches reworded
# spam while leaving genuinely distinct tensions alone.
SIMILARITY_THRESHOLD = 0.85

# Max tensions one non-founder submitter may have in the queue (status open
# or triaged) before further submissions are parked over-cap.
MAX_OPEN_PER_SUBMITTER = 5

# Statuses that occupy the queue (count against the cap).
_QUEUE_STATUSES = frozenset({"open", "triaged"})

# Statuses checked for duplication (queue + already-parked: a flood that
# varies slightly still dedups against its own parked siblings).
_DEDUP_STATUSES = frozenset({"open", "triaged", "parked"})

_NON_WORD = re.compile(r"[^a-z0-9]+")


# ── Text similarity ───────────────────────────────────────────────────────────


def normalize(text: str) -> str:
    """Flatten case, punctuation and whitespace so trivial rewording of the
    same tension doesn't dodge the similarity check."""
    return _NON_WORD.sub(" ", (text or "").lower()).strip()


def similarity(a: str, b: str) -> float:
    """Similarity ratio (0..1) between two texts, over normalized form.

    difflib.SequenceMatcher: deterministic, stdlib, zero deps — the point is
    near-identical detection, not semantic clustering.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


# ── The screening decision ────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntakeDecision:
    """What intake policy says to do with a new tension.

    parked=False → accept into the queue (status 'open', unchanged behaviour).
    parked=True  → accept but park, with a machine-readable reason:
      reason='duplicate' + duplicate_of   → near-identical to an existing
                                            same-submitter tension
      reason='open-cap'                    → submitter over the queue cap
    """

    parked: bool
    reason: str | None = None
    duplicate_of: UUID | None = None


def screen_intake(
    own_tensions: list[TensionRow],
    *,
    title: str,
    description: str,
    is_founder: bool = False,
    threshold: float = SIMILARITY_THRESHOLD,
    cap: int = MAX_OPEN_PER_SUBMITTER,
) -> IntakeDecision:
    """Screen one new tension against its submitter's existing tensions.

    Pure function — the caller (the API route) supplies the submitter's rows
    and executes the decision, which keeps the policy testable without a DB.

    own_tensions: the submitter's tensions on this instance (any status;
    statuses outside the queue/dedup sets are ignored).
    """
    incoming = f"{title}\n{description}"

    # 1. Dedup: near-identical to something this SAME submitter already filed.
    best_ratio, best_row = 0.0, None
    for t in own_tensions:
        if t.status not in _DEDUP_STATUSES:
            continue
        ratio = similarity(incoming, f"{t.title}\n{t.description}")
        if ratio > best_ratio:
            best_ratio, best_row = ratio, t
    if best_row is not None and best_ratio >= threshold:
        return IntakeDecision(
            parked=True, reason="duplicate", duplicate_of=best_row.id,
        )

    # 2. Cap: queue seats held by this submitter (founder exempt — the founder
    # sponsors anonymous submissions and seeds, and is trusted from birth).
    if not is_founder:
        in_queue = sum(1 for t in own_tensions if t.status in _QUEUE_STATUSES)
        if in_queue >= cap:
            return IntakeDecision(parked=True, reason="open-cap")

    return IntakeDecision(parked=False)


__all__ = [
    "IntakeDecision",
    "MAX_OPEN_PER_SUBMITTER",
    "SIMILARITY_THRESHOLD",
    "normalize",
    "screen_intake",
    "similarity",
]
