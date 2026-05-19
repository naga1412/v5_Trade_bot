"""PR10 pure-function helpers: _parse_base_asset, is_stablecoin_pair, is_symbol_allowed."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.trading.symbol_allowlist import (
    _AllowlistCache,
    _parse_base_asset,
    is_stablecoin_pair,
    is_symbol_allowed,
)


def _settings(*, excludes: list[str] | None = None):
    return SimpleNamespace(
        SHADOW_STABLECOIN_EXCLUDE_LIST=excludes or ["USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI"],
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
    )


# --- _parse_base_asset ---------------------------------------------------


def test_parse_base_btcusdt_no_slash() -> None:
    assert _parse_base_asset("BTCUSDT") == "BTC"


def test_parse_base_btc_slash_usdt() -> None:
    assert _parse_base_asset("BTC/USDT") == "BTC"


def test_parse_base_fdusdusdt_longest_quote_first() -> None:
    # FDUSDUSDT → strip USDT (len 4) → "FDUSD" — correctly identifies stablecoin base
    assert _parse_base_asset("FDUSDUSDT") == "FDUSD"


def test_parse_base_busdusdt() -> None:
    assert _parse_base_asset("BUSDUSDT") == "BUSD"


def test_parse_base_dash_separator() -> None:
    assert _parse_base_asset("BTC-USDT") == "BTC"


def test_parse_base_lowercase_normalized() -> None:
    assert _parse_base_asset("btc/usdt") == "BTC"


# --- is_stablecoin_pair --------------------------------------------------


def test_stablecoin_fdusdusdt_excluded() -> None:
    assert is_stablecoin_pair("FDUSDUSDT", _settings()) is True


def test_stablecoin_usdcusdt_excluded() -> None:
    assert is_stablecoin_pair("USDC/USDT", _settings()) is True


def test_non_stablecoin_btc_not_excluded() -> None:
    assert is_stablecoin_pair("BTCUSDT", _settings()) is False


def test_non_stablecoin_eth_not_excluded() -> None:
    assert is_stablecoin_pair("ETH/USDT", _settings()) is False


def test_stablecoin_env_override_adds_more() -> None:
    # Operator adds "USDP" to env list
    custom = _settings(excludes=["USDC", "FDUSD", "USDP"])
    assert is_stablecoin_pair("USDPUSDT", custom) is True
    assert is_stablecoin_pair("BUSDUSDT", custom) is False  # BUSD no longer in list


# --- is_symbol_allowed ---------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    trades_count: int
    sharpe: float | None


def test_allowed_new_symbol_grace_window() -> None:
    """trades_count < grace_trades → allowed regardless of Sharpe."""
    assert is_symbol_allowed(_Snapshot(trades_count=10, sharpe=-5.0)) is True
    assert is_symbol_allowed(_Snapshot(trades_count=49, sharpe=-5.0)) is True


def test_allowed_grace_boundary_50_means_judged_by_sharpe() -> None:
    """trades_count == grace_trades → no longer in grace window."""
    assert is_symbol_allowed(_Snapshot(trades_count=50, sharpe=1.0)) is True
    assert is_symbol_allowed(_Snapshot(trades_count=50, sharpe=-1.0)) is False


def test_allowed_positive_sharpe() -> None:
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=0.5)) is True


def test_allowed_zero_sharpe_excluded() -> None:
    """Sharpe must be strictly positive (> 0, not >=)."""
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=0.0)) is False


def test_allowed_negative_sharpe_excluded() -> None:
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=-2.0)) is False


def test_allowed_none_sharpe_treated_as_zero_excluded() -> None:
    """Sharpe=None (insufficient data) → not allowed past grace window."""
    assert is_symbol_allowed(_Snapshot(trades_count=200, sharpe=None)) is False


# --- _AllowlistCache.is_fresh -------------------------------------------


def test_cache_fresh_within_ttl() -> None:
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    cache = _AllowlistCache(
        snapshot_map={}, last_refresh=now - timedelta(minutes=30),
    )
    assert cache.is_fresh(now) is True


def test_cache_stale_past_ttl() -> None:
    now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    cache = _AllowlistCache(
        snapshot_map={}, last_refresh=now - timedelta(hours=2),
    )
    assert cache.is_fresh(now) is False
