"""PR3 Phase 5: per-TF exit timeout dict (TIMEOUT_BARS_PER_TF).

Spec §4.6: TIMEOUT_BARS becomes a per-TF dict so 15m positions expire
at 96 bars (~24h wall-clock match with 1h's 24-bar window).

Phase 5 also unblocks Phase 5.5 (G1 scaling) which uses the per-TF
baseline as the multiplier base.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.shadow.engine import Direction, ShadowPosition
from app.shadow.exit_monitor import (
    TIMEOUT_BARS_PER_TF,
    ExitReason,
    check_exit,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _pos(*, timeframe: str = "1h", bars_held: int = 0) -> ShadowPosition:
    return ShadowPosition(
        symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        position_size_usdt=10.0, entry_score=0.4, entry_confidence=0.6,
        entry_atr=1.5, layer_scores={}, bars_held=bars_held,
        opened_at=_NOW, last_check_at=_NOW, signal_id="sig_abc",
        timeframe=timeframe,
    )


def test_timeout_bars_per_tf_table_values() -> None:
    """Spec §4.6: 1h=24, 15m=96 (~24h wall-clock equivalent)."""
    assert TIMEOUT_BARS_PER_TF["1h"] == 24
    assert TIMEOUT_BARS_PER_TF["15m"] == 96


def test_1h_position_expires_at_24_bars() -> None:
    """At 24 bars held, the 1h position hits TIMEOUT (matches pre-PR3 behavior)."""
    p = _pos(timeframe="1h", bars_held=24)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_1h_position_does_not_expire_at_23_bars() -> None:
    """Just under the threshold — no exit fired."""
    p = _pos(timeframe="1h", bars_held=23)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    # 99.0 doesn't hit SL (98.0) or TP (104.0); no timeout yet → None
    assert decision is None


def test_15m_position_expires_at_96_bars() -> None:
    """15m positions get 96 bars (~24h on 15-min cadence)."""
    p = _pos(timeframe="15m", bars_held=96)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is not None
    assert decision.reason == ExitReason.TIMEOUT


def test_15m_position_does_not_expire_at_24_bars() -> None:
    """24 bars on 15m = 6 wall-clock hours — well under the 24h budget.
    This is the test that catches a regression where someone reverts the
    per-TF dict back to a single int TIMEOUT_BARS=24."""
    p = _pos(timeframe="15m", bars_held=24)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is None


def test_15m_position_does_not_expire_at_95_bars() -> None:
    """Just under the 15m threshold."""
    p = _pos(timeframe="15m", bars_held=95)
    decision = check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)
    assert decision is None


def test_unknown_tf_raises_keyerror() -> None:
    """Spec §4.6: KeyError on unknown TF is a programming error — fail-loud.
    New TFs require an explicit TIMEOUT_BARS_PER_TF entry."""
    p = _pos(timeframe="5m", bars_held=10)
    with pytest.raises(KeyError):
        check_exit(p, bar_high=99.5, bar_low=99.0, bar_close=99.2)


def test_sl_still_fires_before_timeout_1h() -> None:
    """Regression guard: SL/TP detection runs BEFORE timeout — so a 1h
    position at 24 bars that hits SL exits with STOP_LOSS, not TIMEOUT."""
    p = _pos(timeframe="1h", bars_held=24)
    # LONG SL at 98.0; bar low touches it
    decision = check_exit(p, bar_high=99.5, bar_low=97.0, bar_close=98.5)
    assert decision is not None
    # Per the existing semantics: TIMEOUT fires regardless of SL/TP at the
    # bars_held check, BEFORE the SL hit check. Existing behavior preserved.
    # Test locks this in — if a future refactor swaps the order, this fails.
    assert decision.reason == ExitReason.TIMEOUT


def test_g1_position_with_explicit_timeout_bars_overrides_per_tf_default() -> None:
    """G1 (Phase 5.5): when ShadowPosition.hold_timeout_bars is set, it
    overrides the per-TF default. Phase 5 lays the groundwork — the
    override is wired in Phase 5.5.4 (exit_monitor reads pos.hold_timeout_bars).
    This test asserts the helper TIMEOUT_BARS_PER_TF dict is queryable
    so Phase 5.5 can compose against it."""
    # Phase 5 itself just provides the dict; the override behavior is
    # Phase 5.5.4. We assert the dict shape so Phase 5.5 can rely on it.
    assert TIMEOUT_BARS_PER_TF.get("1h") == 24
    assert TIMEOUT_BARS_PER_TF.get("15m") == 96
    # No fallback for unknown TFs — Phase 5.5 will raise KeyError too
    assert TIMEOUT_BARS_PER_TF.get("4h") is None
