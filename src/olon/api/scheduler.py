"""The in-process schedulers (S7 epochs; G1 governance digests).

Background asyncio tasks in the FastAPI process. Dev-grade: they live and die
with the process, single-process. Upgradable to an external worker later.

epoch_scheduler — for any instance whose CadenceConfig.preset is not 'manual':
  - if an epoch is already running → skip (overlap guard)
  - else pop next_tension; if None → open+close a 'skipped' epoch
  - else open an epoch + fire run_deliberation_live (the worker closes it)

digest_scheduler (G1) — for any instance with governance.digest_interval_h>0:
  - build the governance digest when the last recorded one is older than the
    interval (ledger-driven timing: restart-safe, no state to persist)

All per-instance work is wrapped non-fatal: one instance failing never kills a
scheduler loop. The schedulers hold the process-wide broker + a DB engine.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session as SMSession

from olon.api.live import run_deliberation_live
from olon.config import (
    INSTANCES_DIR,
    load_instance_config,
    load_runtime_config,
)
from olon.store import (
    close_epoch,
    current_epoch,
    make_engine,
    next_tension,
    open_epoch,
)

log = logging.getLogger(__name__)

# How often (seconds) the scheduler scans for instances to tick. Kept short so
# 'realtime' cadence feels responsive in tests; 'daily' instances are checked
# against their own timing within each tick.
_SCAN_INTERVAL = 5.0


def _scheduled_instances() -> list[tuple[str, int]]:
    """Instances whose cadence is non-manual, as (instance_id, interval_seconds).

    Scans the instances/ directory for config files; returns only those with a
    preset that warrants auto-firing. 'daily' uses a 24h interval.
    """
    out: list[tuple[str, int]] = []
    if not INSTANCES_DIR.exists():
        return out
    for path in INSTANCES_DIR.glob("*/instance.yaml"):
        instance_id = path.parent.name
        try:
            ic = load_instance_config(instance_id)
        except Exception:  # noqa: BLE001 — a bad config must not kill the loop
            continue
        if ic.cadence.preset == "realtime" and ic.cadence.interval_seconds > 0:
            out.append((instance_id, ic.cadence.interval_seconds))
        elif ic.cadence.preset == "daily":
            out.append((instance_id, 24 * 3600))
    return out


async def epoch_scheduler(app) -> None:
    """The scheduler loop. Started by create_app's lifespan when at least one
    instance is non-manual. Runs until cancelled (app shutdown)."""
    broker = app.state.broker
    config = load_runtime_config()
    eng = make_engine(config.database_url)
    loop = asyncio.get_running_loop()
    # Per-instance "last-fired" timestamps, so each instance fires on its own
    # interval rather than all at every scan.
    last_fired: dict[str, float] = {}

    log.info("epoch scheduler started")
    try:
        while True:
            now = loop.time()
            for instance_id, interval in _scheduled_instances():
                last = last_fired.get(instance_id, 0.0)
                if now - last < interval:
                    continue
                try:
                    _fire_epoch(instance_id, broker, eng, config, loop)
                except Exception as e:  # noqa: BLE001
                    log.warning("scheduler tick failed for %s (non-fatal): %s", instance_id, e)
                last_fired[instance_id] = now
            await asyncio.sleep(_SCAN_INTERVAL)
    except asyncio.CancelledError:
        log.info("epoch scheduler cancelled (shutdown)")
    finally:
        eng.dispose()


def _fire_epoch(instance_id: str, broker, eng, config, loop) -> None:
    """Fire one epoch for an instance (synchronous DB work; called from the
    async loop). Skips if an epoch is already running (overlap guard)."""
    ic = load_instance_config(instance_id)
    with SMSession(eng) as s:
        if current_epoch(s, instance_id=instance_id) is not None:
            return  # overlap guard
        trow = next_tension(s, instance_id=instance_id)
        if trow is None:
            # No backlog tension → open + immediately close a 'skipped' epoch.
            epoch = open_epoch(s, instance_id=instance_id)
            s.flush()
            close_epoch(s, epoch_id=epoch.id, status="skipped")
            s.commit()
            log.info("scheduler: skipped epoch for %s (empty backlog)", instance_id)
            return
        epoch = open_epoch(s, instance_id=instance_id, tension_id=trow.id)
        s.commit()
        epoch_id = epoch.id

    # Fire the deliberation (the worker starts + closes the epoch).
    run_id = uuid4()
    broker.open(run_id, loop)
    run_deliberation_live(
        instance_id=instance_id, run_id=run_id, broker=broker,
        config=config, instance=ic, epoch_id=epoch_id,
    )
    log.info("scheduler: fired epoch for %s (tension %s)", instance_id, trow.id)


# ── Governance digest scheduler (G1) ─────────────────────────────────────────


def _digest_instances() -> list[tuple[str, float]]:
    """Instances with scheduled governance digests, as
    (instance_id, interval_hours). digest_interval_h <= 0 = off."""
    out: list[tuple[str, float]] = []
    if not INSTANCES_DIR.exists():
        return out
    for path in INSTANCES_DIR.glob("*/instance.yaml"):
        instance_id = path.parent.name
        try:
            ic = load_instance_config(instance_id)
        except Exception:  # noqa: BLE001 — a bad config must not kill the loop
            continue
        if ic.governance.digest_interval_h > 0:
            out.append((instance_id, float(ic.governance.digest_interval_h)))
    return out


def _build_digest(instance_id: str, eng) -> bool:
    """Build + persist one governance digest (synchronous DB/LLM work).
    Returns True if a digest was recorded. The CGA's theme call degrades
    gracefully inside build_governance_digest — a provider outage records the
    facts without themes rather than skipping the day."""
    from olon.api.governance import build_governance_digest

    ic = load_instance_config(instance_id)
    with SMSession(eng) as s:
        digest = build_governance_digest(s, ic=ic)
        s.commit()
    log.info(
        "digest scheduler: recorded governance digest for %s "
        "(pending_attestations=%s decisions=%s)",
        instance_id,
        digest["facts"]["attestations"]["pending_count"],
        digest["facts"]["cycles"]["decisions_recorded"],
    )
    return True


def _digest_due(instance_id: str, eng, *, interval_h: float) -> bool:
    """True when no digest exists yet, or the newest is older than the
    interval. Timing comes from the ledger — restart-safe, no in-memory
    bookkeeping to lose.

    Age is computed in SQL (now() - created_at): row timestamps are stored
    tz-naive in the session timezone, so Python-side UTC arithmetic would be
    off by the server's UTC offset.
    """
    with SMSession(eng) as s:
        got = s.execute(text(
            "SELECT EXTRACT(EPOCH FROM (now() - created_at)) / 3600.0 AS age_h "
            "FROM ledger_event "
            "WHERE instance_id = :i AND event_type = 'governance-digest' "
            "ORDER BY created_at DESC LIMIT 1"
        ), {"i": instance_id}).scalar()
    if got is None:
        return True
    return float(got) >= interval_h


async def digest_scheduler(app) -> None:  # noqa: ARG001 — app for symmetry
    """The G1 digest loop. Started by create_app's lifespan when at least one
    instance opts in (governance.digest_interval_h > 0). Runs until cancelled.

    The blocking build runs in a worker thread so a slow LLM call can't stall
    the event loop (same pattern the epoch scheduler's deliberations follow).
    """
    config = load_runtime_config()
    eng = make_engine(config.database_url)
    loop = asyncio.get_running_loop()
    busy: set[str] = set()  # instances with a build in flight

    log.info("digest scheduler started")
    try:
        while True:
            for instance_id, interval_h in _digest_instances():
                if instance_id in busy:
                    continue
                try:
                    if not _digest_due(instance_id, eng, interval_h=interval_h):
                        continue
                    busy.add(instance_id)
                    await loop.run_in_executor(None, _build_digest, instance_id, eng)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "digest tick failed for %s (non-fatal): %s", instance_id, e,
                    )
                finally:
                    busy.discard(instance_id)
            await asyncio.sleep(_SCAN_INTERVAL)
    except asyncio.CancelledError:
        log.info("digest scheduler cancelled (shutdown)")
    finally:
        eng.dispose()


__all__ = ["digest_scheduler", "epoch_scheduler"]
