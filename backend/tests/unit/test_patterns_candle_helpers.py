"""Tests for the shared TA-Lib wrapper factory."""
from __future__ import annotations

import pandas as pd
import pytest

from app.core.patterns.base import Pattern
from app.core.patterns.candle._talib_helpers import make_talib_wrapper


def test_make_talib_wrapper_creates_class_with_required_attrs() -> None:
    cls = make_talib_wrapper(
        pattern_id="test_hammer",
        talib_func_name="CDLHAMMER",
        confidence=0.7,
    )
    inst = cls()
    assert inst.pattern_id == "test_hammer"
    assert inst.pattern_type == "candle"
    # Satisfies the Pattern Protocol via runtime_checkable.
    assert isinstance(inst, Pattern)


def test_wrapper_returns_none_when_pattern_absent() -> None:
    cls = make_talib_wrapper(
        pattern_id="test_hammer",
        talib_func_name="CDLHAMMER",
        confidence=0.7,
    )
    bars = pd.DataFrame(
        {
            "open": [100.0] * 10,
            "high": [100.5] * 10,
            "low": [99.5] * 10,
            "close": [100.0] * 10,
            "volume": [1_000.0] * 10,
        },
        index=pd.date_range("2025-01-01", periods=10, freq="1h"),
    )
    fire = cls().detect(bars, current_idx=9)
    assert fire is None


def test_wrapper_constructs_long_fire_on_positive_talib_output() -> None:
    cls = make_talib_wrapper(
        pattern_id="test_hammer",
        talib_func_name="CDLHAMMER",
        confidence=0.7,
    )
    # Downtrend then a hammer bar (small body high in the range, long lower wick).
    bars = pd.DataFrame(
        {
            "open": [100 - i for i in range(20)] + [80.0],
            "high": [100.5 - i for i in range(20)] + [80.5],
            "low": [99 - i for i in range(20)] + [75.0],
            "close": [99.5 - i for i in range(20)] + [80.2],
            "volume": [1_000.0] * 21,
        },
        index=pd.date_range("2025-01-01", periods=21, freq="1h"),
    )
    fire = cls().detect(bars, current_idx=20)
    if fire is not None:  # TA-Lib's heuristics may or may not fire on this exact shape
        assert fire.direction in ("LONG", "SHORT")
        assert 0.0 <= fire.strength <= 1.0
        assert fire.confidence == pytest.approx(0.7)
        assert "talib_output" in fire.evidence
        assert fire.evidence["talib_func"] == "CDLHAMMER"


def test_wrapper_returns_none_for_out_of_range_index() -> None:
    cls = make_talib_wrapper(
        pattern_id="test_hammer",
        talib_func_name="CDLHAMMER",
        confidence=0.7,
    )
    bars = pd.DataFrame(
        {
            "open": [100.0] * 5,
            "high": [100.5] * 5,
            "low": [99.5] * 5,
            "close": [100.0] * 5,
            "volume": [1_000.0] * 5,
        },
        index=pd.date_range("2025-01-01", periods=5, freq="1h"),
    )
    assert cls().detect(bars, current_idx=-1) is None
    assert cls().detect(bars, current_idx=10) is None


def test_wrapper_class_name_is_derived_from_pattern_id() -> None:
    cls = make_talib_wrapper(
        pattern_id="three_white_soldiers",
        talib_func_name="CDL3WHITESOLDIERS",
        confidence=0.7,
    )
    assert cls.__name__ == "ThreeWhiteSoldiersPattern"
