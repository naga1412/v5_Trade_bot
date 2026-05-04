"""Five fixed historical regime windows for the eval harness.

Spec sec 5.3 — the model must achieve MAE <= 1.5% on ALL five windows. A single
window failing rejects the checkpoint for activation (spec sec 2 row 13).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class RegimeWindow:
    name: str
    start: datetime
    end: datetime
    description: str


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# Spec sec 5.3 — FROZEN constants.
REGIME_WINDOWS: tuple[RegimeWindow, ...] = (
    RegimeWindow(
        "bull_breakout",
        _utc(2020, 10, 1),
        _utc(2021, 4, 30),
        "Post-COVID rally — sustained uptrend, expanding volatility",
    ),
    RegimeWindow(
        "bear_crash",
        _utc(2022, 4, 1),
        _utc(2022, 12, 31),
        "LUNA + FTX collapses — sustained downtrend, large gaps",
    ),
    RegimeWindow(
        "sideways_grind",
        _utc(2023, 4, 1),
        _utc(2023, 9, 30),
        "Range-bound — no directional bias, mean-reverting",
    ),
    RegimeWindow(
        "high_volatility",
        _utc(2020, 3, 1),
        _utc(2020, 4, 15),
        "COVID crash — extreme volatility, both directions",
    ),
    RegimeWindow(
        "low_volatility",
        _utc(2024, 4, 1),
        _utc(2024, 7, 31),
        "Post-halving compression — tight range, low ATR",
    ),
)


# Spec sec 2 row 13: <= 1.5% MAE on ALL 5 windows.
ACCEPTANCE_MAE_THRESHOLD: float = 0.015


def get_regime(name: str) -> RegimeWindow:
    """Look up a regime window by its name. Raises KeyError if not found."""
    for w in REGIME_WINDOWS:
        if w.name == name:
            return w
    raise KeyError(f"unknown regime: {name!r}")
