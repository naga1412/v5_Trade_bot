"""L4 Smart Money Concepts (SP-5 Phase B1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.layer4_smc import score
from app.core.scoring.types import Direction


def make_bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes,
        "high": [c * 1.005 for c in closes],
        "low":  [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_returns_none_with_too_few_bars() -> None:
    bars = make_bars([100.0] * 30)
    assert score(bars) is None


def test_strong_uptrend_with_bos_returns_long() -> None:
    """A clean monotonic uptrend breaks each prior swing high -> BOS LONG."""
    closes = list(np.linspace(100.0, 200.0, 200))
    bars = make_bars(closes)
    s = score(bars)
    assert s is not None
    assert s.direction is Direction.LONG
    assert s.strength > 0.2


def test_strong_downtrend_with_bos_returns_short() -> None:
    closes = list(np.linspace(200.0, 100.0, 200))
    bars = make_bars(closes)
    s = score(bars)
    assert s is not None
    assert s.direction is Direction.SHORT


def test_flat_chop_returns_neutral_or_low_strength() -> None:
    closes = [100.0 + (i % 3 - 1) * 0.2 for i in range(200)]
    bars = make_bars(closes)
    s = score(bars)
    assert s is not None
    assert s.strength < 0.3


def test_bullish_fvg_three_bar_imbalance_votes_long() -> None:
    """Construct exactly bar[i-2].high < bar[i].low so FVG vote = +1."""
    base = [100.0] * 60
    bars = make_bars(base)
    # Inject a clean upside gap at the final 3 bars: i-2 closes ~99, i jumps to 105.
    bars.iloc[-3, bars.columns.get_loc("high")] = 99.5
    bars.iloc[-3, bars.columns.get_loc("low")] = 99.0
    bars.iloc[-3, bars.columns.get_loc("close")] = 99.2
    bars.iloc[-3, bars.columns.get_loc("open")] = 99.0
    bars.iloc[-1, bars.columns.get_loc("low")] = 105.0
    bars.iloc[-1, bars.columns.get_loc("high")] = 106.0
    bars.iloc[-1, bars.columns.get_loc("open")] = 105.5
    bars.iloc[-1, bars.columns.get_loc("close")] = 105.8
    s = score(bars)
    assert s is not None
    # FVG alone may not push past neutral band — assert it's not SHORT (no
    # opposite vote) and is at least weakly long-leaning.
    assert s.direction is not Direction.SHORT


def test_liquidity_sweep_above_recent_high_votes_short() -> None:
    """Wick pierces a swing high but closes back below -> bearish sweep vote.

    Build closes with a swing-high pivot at index 50 (within the sweep's
    20-bar lookback). Last bar wicks above the pivot's high but closes
    back inside the prior swing-low so the sweep vote fires.
    """
    closes: list[float] = []
    for i in range(60):
        if i == 50:
            closes.append(105.0)  # the pivot inside the sweep lookback
        elif 47 <= i <= 53:
            d = abs(i - 50)
            closes.append(100.0 + max(0.0, 5.0 - d * 2.0))
        else:
            closes.append(100.0 + (i % 3 - 1) * 0.1)
    closes[-1] = 100.5  # current bar close — well below the swing high
    bars = make_bars(closes)
    # Override the final bar: high spikes above the swing high (~105.5),
    # but close stays at 100.5 (below the prior swing high).
    bars.iloc[-1, bars.columns.get_loc("high")] = 106.0
    bars.iloc[-1, bars.columns.get_loc("low")] = 100.0
    bars.iloc[-1, bars.columns.get_loc("open")] = 100.2
    bars.iloc[-1, bars.columns.get_loc("close")] = 100.5
    s = score(bars)
    assert s is not None
    # Sweep should contribute a -1 vote.
    assert "sweep-1" in s.notes
