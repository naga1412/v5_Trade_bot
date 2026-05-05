"""Shared TA-Lib wrapper factory for candle patterns.

Each TA-Lib pattern function (e.g. ``talib.CDLHAMMER``) returns an integer
array in ``{-100, 0, +100}`` per bar. Positive = bullish, negative = bearish,
zero = no detection. We map this to a :class:`PatternFire` with
``strength = abs(value) / 100`` and a fixed ``confidence`` (default 0.7).
Per-pattern modules call :func:`make_talib_wrapper` to get a plug-and-play
:class:`Pattern`-conformant class.
"""
from __future__ import annotations

from typing import cast

import pandas as pd
import talib  # type: ignore[import-untyped]

from app.core.patterns.base import Direction, PatternFire, PatternType


def make_talib_wrapper(
    *,
    pattern_id: str,
    talib_func_name: str,
    confidence: float = 0.7,
) -> type:
    """Build a ``Pattern``-conformant class wrapping ``talib.<talib_func_name>``.

    Args:
        pattern_id: stable snake_case id used as the registry key.
        talib_func_name: the TA-Lib function name (e.g. ``"CDLHAMMER"``).
        confidence: fixed confidence in [0,1] assigned to every fire.

    Returns:
        A class with attributes ``pattern_id`` and ``pattern_type = "candle"``
        and a ``detect(bars, current_idx)`` method returning ``PatternFire``
        or ``None``.
    """
    func = getattr(talib, talib_func_name)

    class _TalibPattern:
        pattern_type: PatternType = "candle"

        def __init__(self) -> None:
            self.pattern_id: str = pattern_id

        def detect(
            self, bars: pd.DataFrame, current_idx: int
        ) -> PatternFire | None:
            if current_idx < 0 or current_idx >= len(bars):
                return None
            try:
                result = func(
                    bars["open"].to_numpy(dtype=float),
                    bars["high"].to_numpy(dtype=float),
                    bars["low"].to_numpy(dtype=float),
                    bars["close"].to_numpy(dtype=float),
                )
            except Exception:  # pragma: no cover — never propagate
                return None
            val = int(result[current_idx])
            if val == 0:
                return None
            direction: Direction = "LONG" if val > 0 else "SHORT"
            strength = min(1.0, abs(val) / 100.0)
            return PatternFire(
                pattern_id=self.pattern_id,
                direction=direction,
                strength=strength,
                confidence=confidence,
                evidence={
                    "talib_output": val,
                    "talib_func": talib_func_name,
                },
            )

    # Friendly class name like "ThreeWhiteSoldiersPattern" derived from id.
    _TalibPattern.__name__ = (
        "".join(part.capitalize() for part in pattern_id.split("_"))
        + "Pattern"
    )
    _TalibPattern.__qualname__ = _TalibPattern.__name__
    return cast(type, _TalibPattern)
