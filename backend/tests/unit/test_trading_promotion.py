"""SP-8 Phase B — promotion gate computation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.trading.promotion import compute_stats


_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


# ---- Empty / degenerate windows ------------------------------------------


def test_empty_window_fails_all_gates() -> None:
    s = compute_stats([], span_days=30, now=_NOW)
    assert s.n_trades == 0
    assert s.passes("telegram-approve") is False
    assert s.passes("fully-auto") is False
    # Failure messages must mention both "trades" + "days" gates explicitly.
    f = s.failures_for("telegram-approve")
    assert any("trades=0" in msg for msg in f)


def test_few_trades_fail_count_gate_but_succeed_on_others() -> None:
    """5 winning trades is below the 100-trade gate but stats are valid."""
    s = compute_stats([2.0, 1.5, 3.0, 1.0, 2.5], span_days=30, now=_NOW)
    f = s.failures_for("telegram-approve")
    assert any("trades=5 < 100" in msg for msg in f)
    # No "win_rate" failure (5/5 wins = 100%)
    assert not any("win_rate" in msg for msg in f)


# ---- Healthy track record ------------------------------------------------


def _fake_winning_track(n: int) -> list[float]:
    """Generate n trades with exactly 55% wins, deeply interleaved.

    Round-robin pattern: W L W W L W L W W L W L W W L W L W W L (20 trades,
    11W + 9L = 55%). No two losses in a row — drawdown stays at 1 trade's
    worth.
    """
    # 11 wins (W) and 9 losses (L) spread across 20 slots so no two L are
    # adjacent — Bresenham-style decimation: at each slot, place an L if
    # the running L-count is below i * 9/20.
    block: list[float] = []
    losses_placed = 0
    for i in range(20):
        target_losses_by_now = (i + 1) * 9 / 20
        if losses_placed < target_losses_by_now and losses_placed < 9:
            block.append(-1.5)
            losses_placed += 1
        else:
            block.append(2.0)
    out: list[float] = []
    while len(out) < n:
        out.extend(block)
    return out[:n]


def test_telegram_approve_passes_when_healthy_30day_track() -> None:
    """100 trades over 30 days, 55% win-rate, mean +0.6% → all gates pass."""
    pnls = _fake_winning_track(100)
    s = compute_stats(pnls, span_days=30, now=_NOW)
    assert s.n_trades == 100
    assert s.win_rate == pytest.approx(0.55)
    # Profit factor: gross_win = 55 * 2.0 = 110; gross_loss = 45 * 1.5 = 67.5
    assert s.profit_factor == pytest.approx(110 / 67.5, rel=1e-3)
    assert s.passes("telegram-approve") is True


def test_fully_auto_requires_300_trades() -> None:
    """Healthy 100 trades passes telegram-approve but fails fully-auto."""
    pnls = _fake_winning_track(100)
    s = compute_stats(pnls, span_days=90, now=_NOW)
    assert s.passes("telegram-approve") is True
    f = s.failures_for("fully-auto")
    assert any("trades=100 < 300" in msg for msg in f)


# ---- Specific gate failures ----------------------------------------------


def test_drawdown_gate_blocks_telegram_approve() -> None:
    """A run with -20% peak-to-trough drawdown fails the 12% gate."""
    pnls = (
        [+2.0] * 10        # cum +20
        + [-3.0] * 8       # cum -4 → drawdown of 24% from peak
        + [+1.0] * 100     # rebuild
    )
    s = compute_stats(pnls, span_days=30, now=_NOW)
    assert s.max_drawdown_pct >= 20.0
    f = s.failures_for("telegram-approve")
    assert any("max_drawdown" in msg for msg in f)


def test_low_win_rate_blocks_gate() -> None:
    """30% win rate fails 40% gate even with positive profit factor."""
    pnls = [+5.0] * 30 + [-1.0] * 70
    s = compute_stats(pnls, span_days=30, now=_NOW)
    assert s.win_rate == pytest.approx(0.30)
    f = s.failures_for("telegram-approve")
    assert any("win_rate" in msg for msg in f)


def test_low_profit_factor_blocks_gate() -> None:
    """PF below 1.5 fails Telegram-approve."""
    # 60 wins of +1, 40 losses of -1 → PF = 60/40 = 1.5 (passes barely)
    # 60 wins of +1, 40 losses of -1.5 → PF = 60/60 = 1.0 (fails)
    pnls = [+1.0] * 60 + [-1.5] * 40
    s = compute_stats(pnls, span_days=30, now=_NOW)
    assert s.profit_factor == pytest.approx(1.0)
    f = s.failures_for("telegram-approve")
    assert any("profit_factor" in msg for msg in f)


def test_negative_sharpe_blocks_gate() -> None:
    """A losing track record produces negative Sharpe → fails."""
    pnls = [-1.0] * 100 + [+0.1] * 5
    s = compute_stats(pnls, span_days=30, now=_NOW)
    assert s.sharpe < 0
    assert s.passes("telegram-approve") is False


# ---- Snapshot serialization ----------------------------------------------


def test_to_dict_round_trips_through_json() -> None:
    import json
    s = compute_stats(_fake_winning_track(100), span_days=30, now=_NOW)
    d = s.to_dict()
    serialized = json.dumps(d)
    assert "telegram-approve" in serialized
    assert "n_trades" in serialized
    parsed = json.loads(serialized)
    assert parsed["n_trades"] == 100


def test_passes_and_failures_for_methods() -> None:
    s = compute_stats(_fake_winning_track(100), span_days=30, now=_NOW)
    assert s.passes("telegram-approve") is True
    assert s.passes("fully-auto") is False
    assert s.failures_for("telegram-approve") == []
    assert len(s.failures_for("fully-auto")) > 0
