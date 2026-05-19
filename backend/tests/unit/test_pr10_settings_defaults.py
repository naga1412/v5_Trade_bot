"""PR10 settings defaults — flag default-OFF; rolling window 100/30; grace 50."""
from __future__ import annotations

from app.config import get_settings


def test_symbol_allowlist_enabled_default_false() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_ENABLED is False


def test_stablecoin_exclude_list_defaults() -> None:
    s = get_settings().SHADOW_STABLECOIN_EXCLUDE_LIST
    assert "USDC" in s
    assert "FDUSD" in s
    assert "USD1" in s
    assert "BUSD" in s
    assert "TUSD" in s
    assert "DAI" in s


def test_grace_trades_default_50() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_GRACE_TRADES == 50


def test_window_trades_default_100() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_WINDOW_TRADES == 100


def test_window_days_default_30() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_WINDOW_DAYS == 30


def test_cache_ttl_default_3600() -> None:
    assert get_settings().SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS == 3600
