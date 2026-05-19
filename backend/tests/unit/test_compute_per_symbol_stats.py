"""PR10 compute_per_symbol_stats — rolling-window per-symbol aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.trading.symbol_allowlist import compute_per_symbol_stats


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Trade:
    symbol: str
    closed_at: datetime
    pnl_usdt: float
    pnl_pct: float


def _settings(window_trades: int = 100, window_days: int = 30, grace: int = 50):
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_WINDOW_TRADES=window_trades,
        SYMBOL_ALLOWLIST_WINDOW_DAYS=window_days,
        SYMBOL_ALLOWLIST_GRACE_TRADES=grace,
    )


def test_per_symbol_grouping() -> None:
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=1), 1.0, 0.01),
        _Trade("BTCUSDT", _NOW - timedelta(hours=2), -0.5, -0.005),
        _Trade("ETHUSDT", _NOW - timedelta(hours=1), 2.0, 0.02),
    ]
    result = compute_per_symbol_stats(trades, _settings(), now=_NOW)
    symbols = {r.symbol for r in result}
    assert symbols == {"BTCUSDT", "ETHUSDT"}


def test_per_symbol_trades_count() -> None:
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), 1.0, 0.01)
        for i in range(5)
    ]
    result = compute_per_symbol_stats(trades, _settings(), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 5


def test_rolling_window_trades_cutoff() -> None:
    """100-trade window: only most-recent 100 per symbol contribute."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), 0.0, 0.0)
        for i in range(150)
    ]
    result = compute_per_symbol_stats(trades, _settings(window_trades=100), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 100


def test_rolling_window_days_cutoff() -> None:
    """30-day window: only trades within last 30 days contribute."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(days=20), 1.0, 0.01),
        _Trade("BTCUSDT", _NOW - timedelta(days=40), 1.0, 0.01),
    ]
    result = compute_per_symbol_stats(trades, _settings(window_days=30), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 1


def test_empty_input_returns_empty_list() -> None:
    assert compute_per_symbol_stats([], _settings(), now=_NOW) == []


def test_allowed_flag_set_per_rule() -> None:
    """trades_count < grace (50) → allowed True regardless of sharpe."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), -1.0, -0.01)
        for i in range(10)
    ]
    result = compute_per_symbol_stats(trades, _settings(), now=_NOW)
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.allowed is True  # grace window


def test_window_uses_smaller_of_trades_or_days() -> None:
    """If 30 days contains 200 trades but window_trades=100, cap at 100."""
    trades = [
        _Trade("BTCUSDT", _NOW - timedelta(hours=i), 0.0, 0.0)
        for i in range(200)
    ]
    result = compute_per_symbol_stats(
        trades, _settings(window_trades=100, window_days=30), now=_NOW,
    )
    btc = next(r for r in result if r.symbol == "BTCUSDT")
    assert btc.trades_count == 100
