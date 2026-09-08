"""Volatility normalization helpers for the live scoring stack (PR1).

Two pure functions:
  - compute_realized_vol_20d: annualized realized vol from 20+ days of 1h bars.
  - compute_effective_score: volatility-normalized score (final × TARGET_VOL / vol).

No I/O. Caller supplies bars and the raw score; this module transforms them.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Protocol

import numpy as np

# ---------------------------------------------------------------------------
# Constants (operator-locked)
# ---------------------------------------------------------------------------

TARGET_VOL: float = 0.02          # ~50% annualized vol = "normal" crypto
MIN_VOL: float = 0.01             # floor to prevent divide-by-near-zero
DAYS_PER_YEAR: int = 365          # used in sqrt(365) annualization
MIN_DAILY_BARS_FOR_VOL: int = 20  # require ≥20 daily bars or return None

# 2026-09-08: this function resamples to CALENDAR DAYS, so the caller's
# bar timeframe determines whether it can ever succeed. At 1h a 504-bar
# buffer spans 21 days and clears the floor. At 15m the same 504 bars
# span 5.25 days -- reaching 20 daily bars would need 1,920 bars, which
# no prewarm value supplies. 15m therefore returns None 100% of the
# time, and did so silently for the column's whole life (341/341 trades
# over 7 days on prod, taking effective_score down with it).
#
# `is_timeframe_supported` exists so callers state that explicitly
# rather than calling into a computation that cannot succeed and
# recording an indistinguishable None. A 100%-NULL column that looks
# like a failure but is a structural mismatch is its own defect class.
_MIN_BARS_FOR_VOL_BY_TIMEFRAME: dict[str, int] = {
    "1h": (MIN_DAILY_BARS_FOR_VOL + 1) * 24,   # 504
    "4h": (MIN_DAILY_BARS_FOR_VOL + 1) * 6,    # 126
    "1d": MIN_DAILY_BARS_FOR_VOL + 1,          # 21
}


def is_timeframe_supported(timeframe: str) -> bool:
    """Can `timeframe` ever supply >=20 calendar days from a live buffer?

    False for sub-hourly timeframes (15m, 5m): the bar count required
    exceeds anything the shadow worker holds. Callers should skip the
    computation rather than record a None that reads as a failure.
    """
    return timeframe in _MIN_BARS_FOR_VOL_BY_TIMEFRAME


def min_bars_for_vol(timeframe: str) -> int | None:
    """Bars of `timeframe` needed to reach MIN_DAILY_BARS_FOR_VOL days.

    None when the timeframe cannot reach it from a live buffer at all.
    Callers sizing a history buffer should use this rather than a
    hardcoded constant -- that coupling is what let the 1h cache-hit
    path accept 200 bars for a computation needing 504.
    """
    return _MIN_BARS_FOR_VOL_BY_TIMEFRAME.get(timeframe)

# Single source of truth for "how many 1h bars a caller must seed before
# compute_realized_vol_20d can return non-None" (2026-08-31 consolidation).
#
# This same requirement was independently declared THREE times --
# app.shadow.worker.HISTORY_BARS, app.ws.live_prediction.HISTORY_SEED_BARS,
# and a bare `limit=504` literal in app.api.routes.tab1 -- and drifted out
# of sync at least twice: PR #400 fixed live_prediction's copy from 300 to
# 504 without touching worker.py's, and a third, independently-discovered
# copy later turned up in tab1.py. Each drift meant `realized_vol_20d`
# silently read back None for whichever caller still held the stale value,
# for as long as nobody noticed. One constant, imported everywhere, is the
# only version of this that can't recur.
#
# (MIN_DAILY_BARS_FOR_VOL + 1) days * 24h -- the +1 day is a safety margin
# over the 20-day floor above, not part of the floor itself.
HISTORY_SEED_BARS_1H: int = (MIN_DAILY_BARS_FOR_VOL + 1) * 24  # = 504


# ---------------------------------------------------------------------------
# Bar protocol — any object with .ts (UTC datetime) and .close (float)
# ---------------------------------------------------------------------------

class _BarLike(Protocol):
    ts: datetime
    close: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_realized_vol_20d(bars: list[Any]) -> float | None:
    """Annualized realized volatility from 20+ days of 1h OHLCV bars.

    Resamples to daily log-returns internally, computes std × sqrt(365).
    Returns None when fewer than 20 daily bars can be derived.

    Args:
        bars: list of objects with at least `.ts` (UTC datetime) and
            `.close` (float). 1h bars expected; the function resamples
            internally — caller does NOT pre-resample.

    Returns:
        Annualized realized vol as a float (e.g. 0.65 = 65% annualized
        for a high-vol crypto). None if insufficient history.
    """
    if not bars:
        return None

    # Group bars by calendar day (UTC), keep last close per day.
    day_to_last_close: dict[Any, float] = {}
    for bar in bars:
        day = bar.ts.date()
        # Overwrite; the final iteration value for each day is the last bar.
        day_to_last_close[day] = float(bar.close)

    # Sort days chronologically so log-returns are in order.
    sorted_days = sorted(day_to_last_close.keys())
    daily_closes = [day_to_last_close[d] for d in sorted_days]

    if len(daily_closes) < MIN_DAILY_BARS_FOR_VOL:
        return None

    # Compute log-returns for consecutive daily closes.
    closes_arr = np.array(daily_closes, dtype=float)
    log_returns = np.log(closes_arr[1:] / closes_arr[:-1])

    # Sample std (ddof=1) then annualize.
    daily_std = float(np.std(log_returns, ddof=1))
    annualized_vol = daily_std * math.sqrt(DAYS_PER_YEAR)
    return annualized_vol


def compute_effective_score(
    final_score: float,
    realized_vol_20d: float | None,
) -> float | None:
    """Volatility-normalized score: final × TARGET_VOL / max(vol, MIN_VOL).

    Higher vol → smaller magnitude (signal is in noisier conditions →
    discount). Lower vol → larger magnitude (clean trend, amplify).

    Args:
        final_score: aggregated raw score from L1-L10 layers, in [-1, +1].
        realized_vol_20d: output of compute_realized_vol_20d, or None.

    Returns:
        Vol-normalized score (still ±-signed); None when realized_vol is None.

    Examples:
        final_score=0.5, vol=0.02 → multiplier = 1.0 → return 0.5
        final_score=0.5, vol=0.04 → multiplier = 0.5 → return 0.25
        final_score=0.5, vol=0.005 → vol clamped to 0.01 → multiplier = 2.0 → return 1.0
        final_score=0.5, vol=None → return None
        final_score=-0.5, vol=0.02 → return -0.5 (sign preserved)
    """
    if realized_vol_20d is None:
        return None

    effective_vol = max(realized_vol_20d, MIN_VOL)
    multiplier = TARGET_VOL / effective_vol
    return final_score * multiplier
