"""Unit tests for the Phase J bot-status Pydantic schemas."""

from datetime import UTC, datetime

import pytest
from app.api.schemas import (
    AssetUniverseEntryOut,
    AssetUniverseOut,
    BotOverviewOut,
    EquityCurveOut,
    EquityCurvePoint,
    GateMetricOut,
    LongShortBreakdownOut,
    OpenPositionOut,
    PerAssetStatOut,
    PromotionGateOut,
    RecentTradeOut,
    WindowStatsOut,
)
from pydantic import ValidationError


def _ws(window: str = "24h") -> WindowStatsOut:
    return WindowStatsOut(
        window=window,  # type: ignore[arg-type]
        trades=10,
        pnl_usdt=42.5,
        pnl_pct=0.05,
        win_rate=0.6,
        sharpe_annualized=1.2,
        max_drawdown=0.04,
        profit_factor=1.8,
    )


def test_window_stats_minimal_ok() -> None:
    w = _ws("7d")
    assert w.window == "7d"
    assert w.trades == 10
    assert w.sharpe_annualized == pytest.approx(1.2)


def test_window_stats_optional_fields_can_be_none() -> None:
    w = WindowStatsOut(
        window="lifetime",
        trades=0,
        pnl_usdt=0.0,
        pnl_pct=None,
        win_rate=0.0,
        sharpe_annualized=None,
        max_drawdown=0.0,
        profit_factor=0.0,
    )
    assert w.pnl_pct is None
    assert w.sharpe_annualized is None


def test_window_stats_invalid_window_rejected() -> None:
    with pytest.raises(ValidationError):
        WindowStatsOut(
            window="6h",  # type: ignore[arg-type]
            trades=1, pnl_usdt=0.0, pnl_pct=0.0,
            win_rate=0.5, sharpe_annualized=None,
            max_drawdown=0.0, profit_factor=1.0,
        )


def test_bot_overview_aggregates_five_windows() -> None:
    overview = BotOverviewOut(
        last_24h=_ws("24h"),
        last_7d=_ws("7d"),
        last_30d=_ws("30d"),
        long_only_30d=_ws("30d"),
        short_only_30d=_ws("30d"),
    )
    assert overview.last_24h.window == "24h"
    assert overview.long_only_30d.trades == 10


def test_gate_metric_operator_validated() -> None:
    m = GateMetricOut(name="win_rate", current=0.5, threshold=0.4,
                      operator=">=", passing=True)
    assert m.passing is True
    with pytest.raises(ValidationError):
        GateMetricOut(name="x", current=1.0, threshold=0.5,
                      operator="==", passing=True)  # type: ignore[arg-type]


def test_promotion_gate_with_metrics_list() -> None:
    metric = GateMetricOut(name="trades", current=50.0, threshold=100.0,
                           operator=">=", passing=False)
    gate = PromotionGateOut(
        target_mode="telegram-approve",
        metrics=[metric],
        all_passing=False,
        distance_summary="50 trades to unlock",
    )
    assert gate.target_mode == "telegram-approve"
    assert len(gate.metrics) == 1


def test_open_position_minimal() -> None:
    pos = OpenPositionOut(
        symbol="BTC/USDT",
        direction="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        position_size_usdt=30.0,
        bars_held=2,
        opened_at=datetime(2026, 5, 1, tzinfo=UTC),
        signal_id="sig-abc",
    )
    assert pos.current_price is None
    assert pos.unrealized_pnl_pct is None
    assert pos.unrealized_pnl_usdt is None


def test_recent_trade_exit_reason_validated() -> None:
    t = RecentTradeOut(
        closed_at=datetime(2026, 5, 1, tzinfo=UTC),
        symbol="BTC/USDT",
        direction="SHORT",
        entry_price=100.0,
        exit_price=95.0,
        pnl_pct=5.0,
        pnl_usdt=1.5,
        exit_reason="TAKE_PROFIT",
        bars_held=4,
        signal_id="sig-xyz",
    )
    assert t.exit_reason == "TAKE_PROFIT"
    with pytest.raises(ValidationError):
        RecentTradeOut(
            closed_at=datetime(2026, 5, 1, tzinfo=UTC),
            symbol="BTC/USDT", direction="LONG", entry_price=100.0,
            exit_price=95.0, pnl_pct=-5.0, pnl_usdt=-1.5,
            exit_reason="MANUAL",  # type: ignore[arg-type]
            bars_held=1, signal_id="x",
        )


def test_long_short_breakdown_holds_two_window_stats() -> None:
    b = LongShortBreakdownOut(long=_ws("30d"), short=_ws("30d"))
    assert b.long.trades == 10
    assert b.short.trades == 10


def test_equity_curve_points() -> None:
    p1 = EquityCurvePoint(
        date=datetime(2026, 5, 1, tzinfo=UTC),
        cumulative_pnl_usdt=10.0,
    )
    p2 = EquityCurvePoint(
        date=datetime(2026, 5, 2, tzinfo=UTC),
        cumulative_pnl_usdt=15.0,
    )
    curve = EquityCurveOut(days=30, points=[p1, p2])
    assert curve.days == 30
    assert len(curve.points) == 2
    assert curve.points[1].cumulative_pnl_usdt == pytest.approx(15.0)


def test_per_asset_stat_optional_sharpe() -> None:
    s = PerAssetStatOut(
        symbol="ETH/USDT",
        trades=3,
        win_rate=0.66,
        avg_rr=2.0,
        pnl_usdt=12.5,
        sharpe_annualized=None,
    )
    assert s.sharpe_annualized is None


def test_asset_universe_out_round_trip() -> None:
    entry = AssetUniverseEntryOut(
        symbol="BTCUSDT", rank=1, quote_volume_24h_usdt=1_234_567.0,
    )
    snap = AssetUniverseOut(
        snapshot_at=datetime(2026, 5, 1, tzinfo=UTC),
        entries=[entry],
    )
    assert snap.entries[0].symbol == "BTCUSDT"
    assert snap.entries[0].rank == 1
