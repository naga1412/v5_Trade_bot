"""Pure-function tests for the breakeven-variant simulator.

Covers the required correctness cases from Amendment 3
(2026-07-31): same-bar tiebreak, arm-then-retrace, arm-then-TP,
never-arm-then-SL, timeout, SHORT symmetry, and empty history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.shadow.breakeven_variant import (
    BarSnapshot,
    simulate_variant_exit,
    variant_name_for,
)
from app.shadow.exit_monitor import ExitReason


_T0 = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)


def _bars(*specs: tuple[float, float, float]) -> list[BarSnapshot]:
    """Compact bar-snapshot builder. Each spec is (high, low, close)."""
    return [
        BarSnapshot(ts=_T0 + timedelta(hours=i), high=h, low=lo, close=c)
        for i, (h, lo, c) in enumerate(specs)
    ]


# ---- LONG: never arms -> baseline behavior identical to check_exit ------


def test_long_sl_hit_before_trigger_stays_stop_loss() -> None:
    """Trigger 0.5R. Entry 100, SL 98 (R=2). Bar 1 dumps to 97.5 (below SL)
    without ever reaching 101 (0.5R trigger). Result: STOP_LOSS at 98,
    armed=False."""
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (100.5, 97.5, 97.8),
        ),
    )
    assert out.exit_reason is ExitReason.STOP_LOSS
    assert out.exit_price == 98.0
    assert out.armed is False
    assert out.armed_bar_index is None
    assert out.bars_held == 1


def test_long_tp_hit_before_trigger_stays_take_profit() -> None:
    """Same setup, bar 1 jumps to 106.5 (past TP) before any low touches
    SL. Result: TAKE_PROFIT at 106, armed=False (peak MFE was reached
    but exit fires before arming has any effect)."""
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (106.5, 99.9, 106.2),
        ),
    )
    assert out.exit_reason is ExitReason.TAKE_PROFIT
    assert out.exit_price == 106.0
    # Trigger crossing is only evaluated AFTER SL/TP check per SL-first
    # convention — TP fires first, so armed remains False.
    assert out.armed is False


# ---- LONG: arms then hits breakeven -------------------------------------


def test_long_arms_then_retrace_to_entry_exits_breakeven() -> None:
    """Entry 100, SL 98, TP 106 (R=2). Bar 1: high 101.5 (crosses 0.5R
    trigger at 101), low 100.5 (well above entry). Bar 2: low 99.5
    (touches entry-armed stop at 100). Result: BREAKEVEN at 100."""
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (101.5, 100.5, 101.2),  # arms mid-bar
            (100.8,  99.5, 99.8),   # low crosses entry
        ),
    )
    assert out.armed is True
    assert out.armed_bar_index == 0
    assert out.exit_reason is ExitReason.BREAKEVEN
    assert out.exit_price == 100.0
    assert out.pnl_pct == 0.0
    assert out.bars_held == 2


def test_long_arms_then_tp_still_tp() -> None:
    """Arms on bar 1, continues up, hits TP on bar 3. Result: TP win
    with armed=True but exit_reason=TAKE_PROFIT (arming is orthogonal
    to which threshold ultimately fires)."""
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (101.5, 100.5, 101.2),
            (103.0, 100.8, 102.7),
            (106.5, 102.5, 106.3),  # TP
        ),
    )
    assert out.armed is True
    assert out.armed_bar_index == 0
    assert out.exit_reason is ExitReason.TAKE_PROFIT
    assert out.exit_price == 106.0
    assert out.bars_held == 3


# ---- LONG: same-bar tiebreak (SL-first on ORIGINAL stop) ----------------


def test_long_same_bar_trigger_and_original_sl_stays_original_sl() -> None:
    """Bar 1: high 101.5 (would arm at trigger 0.5R = 101), low 97.5
    (below original SL 98). SL-first convention: original SL wins THIS
    bar. Arming from this bar's high only affects future bars, but the
    position exited on this bar → armed=False, exit_reason=STOP_LOSS."""
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (101.5, 97.5, 97.8),
        ),
    )
    assert out.exit_reason is ExitReason.STOP_LOSS
    assert out.exit_price == 98.0
    assert out.armed is False


def test_long_armed_prior_bar_then_same_bar_below_entry_and_below_sl() -> None:
    """Bar 1 arms cleanly (high 102, low 100.5). Bar 2 dumps below both
    entry AND the ORIGINAL SL. Because stop was mutated to entry after
    bar 1, this bar's low ≤ stop=entry fires BREAKEVEN — exit at entry,
    not at original SL. The original SL is no longer this position's
    stop; the mechanic converted the loss to 0R."""
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (102.0, 100.5, 101.5),  # arms
            (100.0,  97.0,  97.3),  # crashes below original SL
        ),
    )
    assert out.armed is True
    assert out.armed_bar_index == 0
    assert out.exit_reason is ExitReason.BREAKEVEN
    assert out.exit_price == 100.0


# ---- Timeout -----------------------------------------------------------


def test_timeout_when_neither_sl_nor_tp_hit() -> None:
    """1h timeout limit = 24 bars. Feed 24 lukewarm bars that never
    cross SL/TP and never cross trigger. Result: TIMEOUT at last close,
    armed=False."""
    # 24 bars staying in [99.6, 100.4], entry 100 R=2 (SL 98, TP 106).
    # Neither SL/TP touched; MFE peaks at 0.2R (never crosses 0.5R).
    bars = _bars(*[(100.3, 99.7, 100.0)] * 24)
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=bars,
    )
    assert out.exit_reason is ExitReason.TIMEOUT
    assert out.armed is False
    assert out.bars_held == 24


# ---- SHORT symmetry -----------------------------------------------------


def test_short_arms_then_retrace_exits_breakeven() -> None:
    """SHORT entry 100, SL 102 (R=2), TP 94. Bar 1: low 98.5 (crosses
    0.5R trigger down = 99). Bar 2: high 100.5 (touches entry-armed
    stop). Result: BREAKEVEN at 100."""
    out = simulate_variant_exit(
        direction="SHORT",
        entry_price=100.0,
        initial_stop_loss=102.0,
        take_profit=94.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (99.8, 98.5, 99.0),   # arms
            (100.5, 99.2, 100.2), # high touches entry
        ),
    )
    assert out.armed is True
    assert out.exit_reason is ExitReason.BREAKEVEN
    assert out.exit_price == 100.0
    assert out.pnl_pct == 0.0


def test_short_sl_hit_before_trigger_stays_stop_loss() -> None:
    """SHORT with high jumping to SL before any low reaches trigger."""
    out = simulate_variant_exit(
        direction="SHORT",
        entry_price=100.0,
        initial_stop_loss=102.0,
        take_profit=94.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=_bars(
            (102.5, 99.6, 102.3),
        ),
    )
    assert out.exit_reason is ExitReason.STOP_LOSS
    assert out.armed is False
    assert out.exit_price == 102.0


# ---- Edge cases --------------------------------------------------------


def test_empty_bar_history_returns_timeout_zero_pnl() -> None:
    """No bars observed → treat as immediate timeout at entry (0% pnl,
    0 bars held). Represents restart-recovered positions whose in-memory
    buffer was lost — variant lane emits a benign zero-effect row rather
    than crashing."""
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=[],
    )
    assert out.exit_reason is ExitReason.TIMEOUT
    assert out.exit_price == 100.0
    assert out.pnl_pct == 0.0
    assert out.bars_held == 0
    assert out.armed is False


def test_zero_r_unit_raises() -> None:
    with pytest.raises(ValueError):
        simulate_variant_exit(
            direction="LONG",
            entry_price=100.0,
            initial_stop_loss=100.0,  # R = 0
            take_profit=106.0,
            timeframe="1h",
            trigger_r=0.5,
            bar_history=_bars((101.0, 99.0, 100.5)),
        )


def test_zero_entry_price_raises() -> None:
    with pytest.raises(ValueError):
        simulate_variant_exit(
            direction="LONG",
            entry_price=0.0,
            initial_stop_loss=-1.0,
            take_profit=6.0,
            timeframe="1h",
            trigger_r=0.5,
            bar_history=_bars((1.0, -0.5, 0.5)),
        )


def test_hold_timeout_bars_override_takes_precedence() -> None:
    """If pos.hold_timeout_bars is set (G1 hold-scaling), the simulator
    uses it instead of TIMEOUT_BARS_PER_TF."""
    # 5 lukewarm bars, override limit=3 → timeout on bar 3
    bars = _bars(*[(100.3, 99.7, 100.0)] * 5)
    out = simulate_variant_exit(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        trigger_r=0.5,
        bar_history=bars,
        hold_timeout_bars=3,
    )
    assert out.exit_reason is ExitReason.TIMEOUT
    assert out.bars_held == 3


# ---- 0.40R vs 0.50R lane distinction -----------------------------------


def test_040R_arms_where_050R_does_not() -> None:
    """Confirms dual-lane behavior: a bar whose high crosses 0.40R but
    not 0.50R arms the 0.40 lane and not the 0.50 lane. Same base
    signal → 0.40 exits BREAKEVEN, 0.50 exits STOP_LOSS."""
    # Bar 1: high 100.9 (0.45R), low 99.8 (above SL). Bar 2: low 97.5
    # (below original SL). 0.40 armed on bar 1 → BREAKEVEN at bar 2.
    # 0.50 never armed → STOP_LOSS at bar 2 (original SL).
    bars = _bars(
        (100.9, 99.8, 100.5),
        (100.0, 97.5, 97.8),
    )
    common = dict(
        direction="LONG",
        entry_price=100.0,
        initial_stop_loss=98.0,
        take_profit=106.0,
        timeframe="1h",
        bar_history=bars,
    )
    out_40 = simulate_variant_exit(trigger_r=0.40, **common)
    out_50 = simulate_variant_exit(trigger_r=0.50, **common)
    assert out_40.armed is True
    assert out_40.exit_reason is ExitReason.BREAKEVEN
    assert out_40.exit_price == 100.0
    assert out_50.armed is False
    assert out_50.exit_reason is ExitReason.STOP_LOSS
    assert out_50.exit_price == 98.0


# ---- variant_name_for canonical form -----------------------------------


def test_variant_name_canonical_form() -> None:
    assert variant_name_for(0.40) == "breakeven_0.40R"
    assert variant_name_for(0.50) == "breakeven_0.50R"
    assert variant_name_for(0.5) == "breakeven_0.50R"
