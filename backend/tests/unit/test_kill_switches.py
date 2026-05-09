"""SP-8 Phase D — kill switches + auto-demote logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from app.trading.kill_switches import (
    DEFAULTS,
    SwitchConfig,
    evaluate_auto_demote,
    evaluate_consecutive_losses,
    evaluate_daily_loss,
    evaluate_funding_rate,
    evaluate_liquidation_near,
    evaluate_network_outage,
    evaluate_slippage,
)


_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)


def _cfg(name: str, *, enabled: bool = True, threshold: float | None = None) -> SwitchConfig:
    return SwitchConfig(name=name, enabled=enabled, threshold_value=threshold)  # type: ignore[arg-type]


# ---- Daily loss -----------------------------------------------------------


def test_daily_loss_does_not_trip_when_disabled() -> None:
    r = evaluate_daily_loss(
        portfolio_value_usdt=10_000, realized_pnl_today_usdt=-1_000,
        cfg=_cfg("daily_loss", enabled=False), now=_NOW,
    )
    assert r.tripped is False


def test_daily_loss_trips_at_2pct_when_below_dollar_floor() -> None:
    """Portfolio $5000 → 2% = $100 < $200 absolute floor → uses $100."""
    r = evaluate_daily_loss(
        portfolio_value_usdt=5_000, realized_pnl_today_usdt=-101,
        cfg=_cfg("daily_loss"), now=_NOW,
    )
    assert r.tripped is True
    assert r.reason and "$100" in r.reason


def test_daily_loss_trips_at_dollar_floor_for_large_portfolio() -> None:
    """Portfolio $50_000 → 2% = $1000; capped at absolute $200 floor."""
    r = evaluate_daily_loss(
        portfolio_value_usdt=50_000, realized_pnl_today_usdt=-201,
        cfg=_cfg("daily_loss"), now=_NOW,
    )
    assert r.tripped is True
    assert r.reason and "$200" in r.reason


def test_daily_loss_does_not_trip_at_threshold_minus_one() -> None:
    """A $99 loss on $5000 should NOT trip the $100 limit."""
    r = evaluate_daily_loss(
        portfolio_value_usdt=5_000, realized_pnl_today_usdt=-99,
        cfg=_cfg("daily_loss"), now=_NOW,
    )
    assert r.tripped is False


# ---- Consecutive losses --------------------------------------------------


def test_consecutive_losses_default_is_five() -> None:
    """All 5 last trades negative → trip."""
    r = evaluate_consecutive_losses(
        recent_trade_pnls=[-1, -2, -1, -3, -1],
        cfg=_cfg("consecutive_losses"),
    )
    assert r.tripped is True
    assert "5" in (r.reason or "")


def test_consecutive_losses_one_win_in_tail_doesnt_trip() -> None:
    r = evaluate_consecutive_losses(
        recent_trade_pnls=[-1, -2, +0.5, -1, -1],
        cfg=_cfg("consecutive_losses"),
    )
    assert r.tripped is False


def test_consecutive_losses_under_window_doesnt_trip() -> None:
    """Only 3 trades total — can't have 5 consecutive."""
    r = evaluate_consecutive_losses(
        recent_trade_pnls=[-1, -1, -1],
        cfg=_cfg("consecutive_losses"),
    )
    assert r.tripped is False


def test_consecutive_losses_custom_threshold_three() -> None:
    r = evaluate_consecutive_losses(
        recent_trade_pnls=[-1, -1, -1],
        cfg=_cfg("consecutive_losses", threshold=3),
    )
    assert r.tripped is True


# ---- Network outage ------------------------------------------------------


def test_network_outage_trips_at_61s() -> None:
    r = evaluate_network_outage(
        last_heartbeat_at=_NOW - timedelta(seconds=61),
        cfg=_cfg("network_outage"), now=_NOW,
    )
    assert r.tripped is True


def test_network_outage_no_trip_at_59s() -> None:
    r = evaluate_network_outage(
        last_heartbeat_at=_NOW - timedelta(seconds=59),
        cfg=_cfg("network_outage"), now=_NOW,
    )
    assert r.tripped is False


# ---- Slippage ------------------------------------------------------------


def test_slippage_trips_at_one_pct_when_default_is_half_pct() -> None:
    r = evaluate_slippage(
        expected_price=100.0, actual_fill_price=101.0,
        cfg=_cfg("slippage"),
    )
    assert r.tripped is True


def test_slippage_no_trip_at_quarter_pct() -> None:
    r = evaluate_slippage(
        expected_price=100.0, actual_fill_price=100.25,
        cfg=_cfg("slippage"),
    )
    assert r.tripped is False


def test_slippage_invariant_to_direction() -> None:
    r_up = evaluate_slippage(
        expected_price=100, actual_fill_price=102, cfg=_cfg("slippage"),
    )
    r_down = evaluate_slippage(
        expected_price=100, actual_fill_price=98, cfg=_cfg("slippage"),
    )
    assert r_up.tripped is True
    assert r_down.tripped is True


# ---- Liquidation-near ---------------------------------------------------


def test_liquidation_near_trips_when_buffer_below_50pct() -> None:
    """Entry $100, liquidation $90 (10% distance). Current $95 → buffer
    = 5/10 = 50%. Below 50% threshold → trips."""
    r = evaluate_liquidation_near(
        current_price=94, liquidation_price=90, entry_price=100,
        cfg=_cfg("liquidation_near"),
    )
    assert r.tripped is True


def test_liquidation_near_no_trip_when_buffer_above_threshold() -> None:
    r = evaluate_liquidation_near(
        current_price=99, liquidation_price=90, entry_price=100,
        cfg=_cfg("liquidation_near"),
    )
    assert r.tripped is False


def test_liquidation_near_handles_zero_distance() -> None:
    """Defensive: if entry == liquidation, no trip."""
    r = evaluate_liquidation_near(
        current_price=100, liquidation_price=100, entry_price=100,
        cfg=_cfg("liquidation_near"),
    )
    assert r.tripped is False


# ---- Funding rate guard --------------------------------------------------


def test_funding_rate_blocks_new_long_when_funding_high() -> None:
    """Funding 2 %/day > 1 % threshold on LONG → trips for new LONG."""
    r = evaluate_funding_rate(
        daily_funding_rate=0.02, position_direction="LONG",
        cfg=_cfg("funding_rate_guard"),
    )
    assert r.tripped is True


def test_funding_rate_does_not_trip_short_when_funding_positive() -> None:
    """Positive funding helps SHORT; only blocks LONG."""
    r = evaluate_funding_rate(
        daily_funding_rate=0.02, position_direction="SHORT",
        cfg=_cfg("funding_rate_guard"),
    )
    assert r.tripped is False


def test_funding_rate_blocks_new_short_when_funding_very_negative() -> None:
    """Negative funding helps LONG and hurts SHORT; -2 % blocks new SHORT."""
    r = evaluate_funding_rate(
        daily_funding_rate=-0.02, position_direction="SHORT",
        cfg=_cfg("funding_rate_guard"),
    )
    assert r.tripped is True


# ---- Auto-demote --------------------------------------------------------


def test_auto_demote_no_signals_no_demotion() -> None:
    s = evaluate_auto_demote(
        daily_loss_pct=0.0, consecutive_losses=0,
        days_below_gates=0, audit_chain_broken=False,
    )
    assert s.fully_to_telegram is False
    assert s.telegram_to_manual is False
    assert s.reasons == []


def test_auto_demote_daily_loss_5pct_demotes_fully_to_telegram() -> None:
    s = evaluate_auto_demote(
        daily_loss_pct=-0.06, consecutive_losses=0,
        days_below_gates=0, audit_chain_broken=False,
    )
    assert s.fully_to_telegram is True
    assert s.telegram_to_manual is False
    assert any("daily loss" in r for r in s.reasons)


def test_auto_demote_10_consecutive_losses_demotes_fully_to_telegram() -> None:
    s = evaluate_auto_demote(
        daily_loss_pct=0.0, consecutive_losses=10,
        days_below_gates=0, audit_chain_broken=False,
    )
    assert s.fully_to_telegram is True
    assert any("consecutive" in r for r in s.reasons)


def test_auto_demote_7_days_below_gates_demotes_telegram_to_manual() -> None:
    s = evaluate_auto_demote(
        daily_loss_pct=0.0, consecutive_losses=0,
        days_below_gates=7, audit_chain_broken=False,
    )
    assert s.fully_to_telegram is False
    assert s.telegram_to_manual is True


def test_auto_demote_audit_chain_broken_demotes_to_manual() -> None:
    """Worst case: any mode → Manual + freeze."""
    s = evaluate_auto_demote(
        daily_loss_pct=0.0, consecutive_losses=0,
        days_below_gates=0, audit_chain_broken=True,
    )
    assert s.telegram_to_manual is True
    assert s.fully_to_telegram is True
    assert any("audit chain" in r for r in s.reasons)


# ---- Defaults exposed for UI display -------------------------------------


def test_defaults_dict_contains_all_six_switches() -> None:
    expected = {
        "daily_loss", "consecutive_losses", "network_outage",
        "slippage", "liquidation_near", "funding_rate_guard",
    }
    assert set(DEFAULTS.keys()) == expected
