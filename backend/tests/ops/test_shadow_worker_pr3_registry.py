"""PR3 Phase 6: shadow_worker registry entry tightening.

Spec §4.7 + §6.3 hard bound: B7 reduces max_staleness_seconds from
2h to 30 min to match the 15m cadence. B6 + B7 ship together — B6
(heartbeat wired in _handle_candle) was already closed by FU-1; B7
is what this commit enforces.
"""
from __future__ import annotations

from app.ops.worker_registry import by_name


def test_shadow_worker_max_staleness_30min() -> None:
    """Was 2h (2*60*60=7200s); PR3 tightens to 30min (30*60=1800s)."""
    spec = by_name("shadow_worker")
    assert spec is not None
    assert spec.max_staleness_seconds == 30 * 60, (
        f"shadow_worker max_staleness_seconds must be 30 min for the 15m "
        f"cadence; got {spec.max_staleness_seconds}s"
    )


def test_shadow_worker_pending_heartbeat_off() -> None:
    """Spec §6.3 + the no_pending_heartbeat_after_fu1 CI gate: PR3 must
    NOT regress the FU-1 closure for shadow_worker."""
    spec = by_name("shadow_worker")
    assert spec is not None
    assert spec.pending_heartbeat is False


def test_shadow_worker_remains_stateful() -> None:
    """Holds open positions in memory; never auto-restart."""
    spec = by_name("shadow_worker")
    assert spec is not None
    assert spec.stateful is True


def test_shadow_worker_description_mentions_multi_tf() -> None:
    """Operator-facing description should signal the multi-TF behavior."""
    spec = by_name("shadow_worker")
    assert spec is not None
    # Either '15m' or 'multi-tf' substring is acceptable
    desc = spec.description.lower()
    assert "15m" in desc or "multi-tf" in desc or "multi tf" in desc
