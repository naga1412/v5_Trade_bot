"""Indicator registry test (SP-2 Phase E, task E0).

The registry must re-export every public indicator function so callers can do
``from app.core.indicators import rsi, macd, ema, ...``. The plan's "43
indicators" prose counts each output line — bollinger upper/middle/lower
counts as 3, MACD line/signal/hist as 3, etc. — but the *module count* is
**34**: 3 SP-0 baseline (ema, macd, rsi) + 31 SP-2 Phase B additions.
The test asserts the module count since that's what the registry is.
"""
from __future__ import annotations

from app.core import indicators as ind


def test_all_indicators_count_is_34() -> None:
    """3 SP-0 baseline (ema, macd, rsi) + 31 SP-2 Phase B = 34 modules."""
    assert len(ind.ALL_INDICATORS) == 34


def test_all_indicators_entries_are_callables() -> None:
    for name, fn in ind.ALL_INDICATORS.items():
        assert callable(fn), f"{name} is not callable"
        assert isinstance(name, str) and name


def test_all_indicators_are_reexported_at_package_root() -> None:
    """Every entry in ALL_INDICATORS must be importable as ``ind.<name>``."""
    for name in ind.ALL_INDICATORS:
        assert hasattr(ind, name), f"{name} not exported on the indicators package"


def test_baseline_sp0_indicators_present() -> None:
    """SP-0 baseline (ema/macd/rsi) still importable after the SP-2 fan-out."""
    for name in ("ema", "macd", "rsi"):
        assert name in ind.ALL_INDICATORS


def test_sp2_phase_b_additions_present() -> None:
    """A representative subset of the 31 SP-2 additions wired into the registry."""
    for name in (
        "adx", "aroon", "atr", "awesome_oscillator", "bollinger", "cci",
        "chaikin_money_flow", "dema", "donchian", "dpo", "ease_of_movement",
        "force_index", "hull_ma", "ichimoku", "kama", "keltner", "kvo",
        "mass_index", "mfi", "obv", "psar", "roc", "sma", "stochastic",
        "tema", "trix", "tsi", "ultimate", "vortex", "vwap", "williams_r",
    ):
        assert name in ind.ALL_INDICATORS, f"missing SP-2 indicator: {name}"


def test_registry_keys_are_alphabetical() -> None:
    keys = list(ind.ALL_INDICATORS.keys())
    assert keys == sorted(keys)
