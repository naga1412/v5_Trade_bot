"""Breakeven-stop variant simulator — pure function.

Amendment 3 (2026-07-31) to the breakeven-stop design. See
`docs/superpowers/plans/2026-07-30-breakeven-stop-mechanic-design.md`
for architecture, table shape, and confirmations (variant lane
writes no cooldowns, does not touch open_shadow_positions, is
paired to the base signal via `base_shadow_trade_id`).

`simulate_variant_exit` walks the base position's bar history and
returns what the position would have done if a breakeven stop had
been armed once peak-MFE crossed `trigger_r × |entry - initial_SL|`.

Same-bar tiebreak convention: SL-first on the ORIGINAL stop
(matches production convention at exit_monitor.py:55). If the
original SL would have fired same-bar as the trigger crossing, the
original SL still wins. Same-bar breakeven arming only affects
FUTURE bars.

Timeout: same TIMEOUT_BARS_PER_TF table used by base check_exit.
When `bars_held >= timeout_bars`, exit at bar_close with reason
TIMEOUT.

Pure — no DB, no I/O, no logging inside the loop. Failures raise
exceptions the caller catches so the base close is not disrupted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.shadow.exit_monitor import ExitReason, TIMEOUT_BARS_PER_TF


@dataclass(frozen=True)
class BarSnapshot:
    """One candle in the base position's bar history buffer."""

    ts: datetime
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class VariantOutcome:
    """Result of one variant simulation."""

    exit_price: float
    exit_reason: ExitReason
    exit_ts: datetime
    bars_held: int
    pnl_pct: float
    armed: bool
    armed_bar_index: int | None  # 0-based index into bar_history when arm fired


def _pnl_pct(
    *,
    direction: Literal["LONG", "SHORT"],
    entry_price: float,
    exit_price: float,
) -> float:
    """Gross % vs entry. Matches the shadow_trades.pnl_pct formula used
    by build_shadow_trade_payload (before-fee, exit-vs-entry ratio)."""
    if entry_price <= 0:
        return 0.0
    if direction == "LONG":
        return (exit_price - entry_price) / entry_price * 100.0
    return (entry_price - exit_price) / entry_price * 100.0


def simulate_variant_exit(
    *,
    direction: Literal["LONG", "SHORT"],
    entry_price: float,
    initial_stop_loss: float,
    take_profit: float,
    timeframe: str,
    trigger_r: float,
    bar_history: list[BarSnapshot],
    hold_timeout_bars: int | None = None,
) -> VariantOutcome:
    """Replay bar_history under a breakeven-armed alternative.

    - Walks bars in order (bar_history[0] is the first candle after
      entry — i.e. the same bar order the base worker processed).
    - Tracks peak-MFE in R-units where R = |entry - initial_stop_loss|.
    - When peak-MFE crosses `trigger_r`, ARMS the breakeven for
      subsequent bars (stop_loss becomes entry_price).
    - Same-bar tiebreak: if the bar's original-SL condition fires,
      the original SL wins (SL-first convention). Arming from the
      same bar's high only affects FUTURE bars.
    - Timeout: `hold_timeout_bars or TIMEOUT_BARS_PER_TF[timeframe]`.

    Args:
        direction: "LONG" or "SHORT".
        entry_price: The signal's entry price (base's `pos.entry_price`).
        initial_stop_loss: The signal's ORIGINAL stop (before any
            base-lane mutation — which cannot happen today; asserted
            invariant).
        take_profit: The signal's TP (unchanged in this mechanic).
        timeframe: "1h" / "15m" etc — used for TIMEOUT_BARS lookup.
        trigger_r: Multiplier on R. 0.40 or 0.50 initially.
        bar_history: Ordered bars, each as (ts, high, low, close).
            First entry is the FIRST bar after entry (not the entry
            bar itself). Length ≥ 0 acceptable; length 0 → TIMEOUT
            at entry_price (0% pnl).
        hold_timeout_bars: Overrides TIMEOUT_BARS_PER_TF when set
            (G1 hold-scaling). Mirrors check_exit semantics.

    Returns:
        VariantOutcome. `armed=False` + exit_reason ∈
        {STOP_LOSS, TAKE_PROFIT, TIMEOUT} when the trigger never
        crossed — outcome identical to base.
        `armed=True` + exit_reason=BREAKEVEN when the stop was moved
        to entry AND a later bar's low touched entry.
    """
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    r_unit = abs(entry_price - initial_stop_loss)
    if r_unit <= 0:
        raise ValueError("initial_stop_loss cannot equal entry_price")
    if trigger_r <= 0:
        raise ValueError("trigger_r must be > 0")

    # Empty history → timeout-at-entry semantics (no bars seen; treat
    # as immediate close at entry_price with 0% pnl and 0 bars).
    if not bar_history:
        return VariantOutcome(
            exit_price=entry_price,
            exit_reason=ExitReason.TIMEOUT,
            exit_ts=datetime.min,
            bars_held=0,
            pnl_pct=0.0,
            armed=False,
            armed_bar_index=None,
        )

    limit = hold_timeout_bars if hold_timeout_bars is not None else \
        TIMEOUT_BARS_PER_TF[timeframe]  # KeyError → fail-loud

    armed = False
    armed_at: int | None = None
    stop = initial_stop_loss  # will be mutated to entry when armed
    peak_mfe_r = 0.0

    for i, bar in enumerate(bar_history):
        bars_held = i + 1

        # ---- Same-bar tiebreak: original-SL check on the CURRENT stop
        # ---- BEFORE arming from this bar's high. If the position was
        # ---- already armed before this bar, `stop == entry_price`.
        if direction == "LONG":
            sl_hit = bar.low <= stop
            tp_hit = bar.high >= take_profit
            if sl_hit:
                reason = (
                    ExitReason.BREAKEVEN if armed else ExitReason.STOP_LOSS
                )
                return VariantOutcome(
                    exit_price=stop,
                    exit_reason=reason,
                    exit_ts=bar.ts,
                    bars_held=bars_held,
                    pnl_pct=_pnl_pct(
                        direction="LONG",
                        entry_price=entry_price,
                        exit_price=stop,
                    ),
                    armed=armed,
                    armed_bar_index=armed_at,
                )
            if tp_hit:
                return VariantOutcome(
                    exit_price=take_profit,
                    exit_reason=ExitReason.TAKE_PROFIT,
                    exit_ts=bar.ts,
                    bars_held=bars_held,
                    pnl_pct=_pnl_pct(
                        direction="LONG",
                        entry_price=entry_price,
                        exit_price=take_profit,
                    ),
                    armed=armed,
                    armed_bar_index=armed_at,
                )
        else:  # SHORT
            sl_hit = bar.high >= stop
            tp_hit = bar.low <= take_profit
            if sl_hit:
                reason = (
                    ExitReason.BREAKEVEN if armed else ExitReason.STOP_LOSS
                )
                return VariantOutcome(
                    exit_price=stop,
                    exit_reason=reason,
                    exit_ts=bar.ts,
                    bars_held=bars_held,
                    pnl_pct=_pnl_pct(
                        direction="SHORT",
                        entry_price=entry_price,
                        exit_price=stop,
                    ),
                    armed=armed,
                    armed_bar_index=armed_at,
                )
            if tp_hit:
                return VariantOutcome(
                    exit_price=take_profit,
                    exit_reason=ExitReason.TAKE_PROFIT,
                    exit_ts=bar.ts,
                    bars_held=bars_held,
                    pnl_pct=_pnl_pct(
                        direction="SHORT",
                        entry_price=entry_price,
                        exit_price=take_profit,
                    ),
                    armed=armed,
                    armed_bar_index=armed_at,
                )

        # ---- Neither SL nor TP hit this bar. Update peak-MFE from the
        # ---- bar's favorable extreme, then check trigger crossing.
        if direction == "LONG":
            favorable_r = (bar.high - entry_price) / r_unit
        else:
            favorable_r = (entry_price - bar.low) / r_unit
        if favorable_r > peak_mfe_r:
            peak_mfe_r = favorable_r
        if not armed and peak_mfe_r >= trigger_r:
            armed = True
            armed_at = i
            stop = entry_price  # breakeven — applies to FUTURE bars

        # ---- Timeout check (mirrors check_exit ordering: SL/TP first,
        # ---- then timeout). Exit at bar_close.
        if bars_held >= limit:
            return VariantOutcome(
                exit_price=bar.close,
                exit_reason=ExitReason.TIMEOUT,
                exit_ts=bar.ts,
                bars_held=bars_held,
                pnl_pct=_pnl_pct(
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=bar.close,
                ),
                armed=armed,
                armed_bar_index=armed_at,
            )

    # Bar history exhausted without SL/TP/timeout — treat as timeout at
    # the last observed close. This can happen when the caller passes
    # a truncated buffer or the base closed for reasons the simulator
    # doesn't model (e.g. G1 rescoring — not on this mechanic's path).
    last = bar_history[-1]
    return VariantOutcome(
        exit_price=last.close,
        exit_reason=ExitReason.TIMEOUT,
        exit_ts=last.ts,
        bars_held=len(bar_history),
        pnl_pct=_pnl_pct(
            direction=direction,
            entry_price=entry_price,
            exit_price=last.close,
        ),
        armed=armed,
        armed_bar_index=armed_at,
    )


def variant_name_for(trigger_r: float) -> str:
    """Canonical variant_name for a given trigger. Used as the
    `shadow_trade_variants.variant_name` value AND as part of the
    UNIQUE constraint (base_shadow_trade_id, variant_name)."""
    return f"breakeven_{trigger_r:.2f}R"
