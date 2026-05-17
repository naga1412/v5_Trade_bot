"""PR3 Phase 5.5.4: exit_monitor reads pos.hold_timeout_bars when set.

When HOLD_TP_SCALING_ENABLED is True at trade-open, the worker writes
pos.hold_timeout_bars onto the position. exit_monitor.check_exit honors
that override before falling back to the per-TF baseline.

Locks the override semantic in. Phase 5 alone covers the per-TF
baseline path; this test covers the G1 override layered on top.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import ExitReason, check_exit


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _pos(
    *, timeframe: str = "1h",
    bars_held: int = 0,
    hold_timeout_bars: int | None = None,
    hold_scaling_factor: float | None = None,
) -> ShadowPosition:
    return ShadowPosition(
        symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        position_size_usdt=10.0, entry_score=0.4, entry_confidence=0.6,
        entry_atr=1.5, layer_scores={}, bars_held=bars_held,
        opened_at=_NOW, last_check_at=_NOW, signal_id="sig_abc",
        timeframe=timeframe,
        hold_scaling_factor=hold_scaling_factor,
        hold_timeout_bars=hold_timeout_bars,
    )


def test_g1_override_extends_1h_timeout_beyond_baseline() -> None:
    """G1 mtf_agreement=5 → hold_timeout_bars=96 on a 1h trade.
    Now the position survives past bars_held=24 (pre-G1 timeout) and
    only fires TIMEOUT at bars_held=96."""
    # At 24 bars (pre-G1 1h baseline), G1-scaled position is NOT yet expired
    p = _pos(timeframe="1h", bars_held=24, hold_timeout_bars=96,
             hold_scaling_factor=1.5)
    assert check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2) is None

    # At 96 bars (G1 limit), it expires
    p = _pos(timeframe="1h", bars_held=96, hold_timeout_bars=96,
             hold_scaling_factor=1.5)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_g1_override_extends_15m_timeout_beyond_per_tf_baseline() -> None:
    """G1 mtf_agreement=4 on 15m → hold_timeout_bars=192. Survives past
    96 (15m baseline) and only fires at 192."""
    p = _pos(timeframe="15m", bars_held=96, hold_timeout_bars=192,
             hold_scaling_factor=1.25)
    assert check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2) is None

    p = _pos(timeframe="15m", bars_held=192, hold_timeout_bars=192,
             hold_scaling_factor=1.25)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_no_override_falls_back_to_per_tf_baseline() -> None:
    """Pre-G1 trade (hold_timeout_bars=None) uses TIMEOUT_BARS_PER_TF[tf]
    exactly as Phase 5 specifies. This is the bit-identical-with-PR2 path."""
    p = _pos(timeframe="1h", bars_held=24, hold_timeout_bars=None,
             hold_scaling_factor=None)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_override_can_shorten_below_per_tf_baseline() -> None:
    """Edge case: if a future operator sets HOLD_TP_SCALING_TABLE so the
    scaled bars are LESS than the per-TF baseline (e.g. agreement=3 → 12
    bars for some experimental config), the override still wins — the
    explicit per-position value takes precedence. Phase 5.5.4 contract."""
    p = _pos(timeframe="1h", bars_held=12, hold_timeout_bars=12,
             hold_scaling_factor=0.5)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT
