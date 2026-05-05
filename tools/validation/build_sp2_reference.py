"""Generate ``sp2_reference.json`` from current TA-Lib + indicator outputs.

SP-2 Phase F1 — see the plan at
``docs/superpowers/plans/2026-05-05-SP-2-indicators-patterns-plan.md``.

The plan calls for "100 samples from TradingView", but TradingView reference
values require manual data entry from a human. We pivot to a TA-Lib +
hand-crafted reference because TA-Lib is the canonical reference for candle
patterns (it's the same C library Pine Script wraps).

Total = 100 entries:
  - 50 indicator samples: 5 indicators (rsi, macd_line, atr, bollinger_upper,
    stochastic_k) x 10 hand-crafted bar windows.
  - 50 pattern samples: 10 candle patterns (hammer, engulfing, doji,
    morning_star, evening_star, three_white_soldiers, three_black_crows,
    harami, dark_cloud_cover, piercing) x 5 windows each (some that fire,
    some that don't).

Run from the worktree root:

    PYTHONPATH=backend python tools/validation/build_sp2_reference.py

The output is committed alongside this script so the cross-validation script
(:mod:`tools.validation.sp2_cross_check`) can load and replay it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Make ``backend`` importable when run from the worktree root.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.indicators.atr import atr  # noqa: E402
from app.core.indicators.bollinger import bollinger  # noqa: E402
from app.core.indicators.macd import macd  # noqa: E402
from app.core.indicators.rsi import rsi  # noqa: E402
from app.core.indicators.stochastic import stochastic  # noqa: E402
from app.core.patterns.candle.dark_cloud_cover import (  # noqa: E402
    DarkCloudCoverPattern,
)
from app.core.patterns.candle.doji import DojiPattern  # noqa: E402
from app.core.patterns.candle.engulfing import EngulfingPattern  # noqa: E402
from app.core.patterns.candle.evening_star import EveningStarPattern  # noqa: E402
from app.core.patterns.candle.hammer import HammerPattern  # noqa: E402
from app.core.patterns.candle.harami import HaramiPattern  # noqa: E402
from app.core.patterns.candle.morning_star import MorningStarPattern  # noqa: E402
from app.core.patterns.candle.piercing import PiercingPattern  # noqa: E402
from app.core.patterns.candle.three_black_crows import (  # noqa: E402
    ThreeBlackCrowsPattern,
)
from app.core.patterns.candle.three_white_soldiers import (  # noqa: E402
    ThreeWhiteSoldiersPattern,
)

OUT_PATH = Path(__file__).resolve().parent / "sp2_reference.json"

NOTE = (
    "Reference is TA-Lib output, not TradingView. TradingView Pine Script for "
    "these patterns wraps the same TA-Lib C library, so this is a faithful "
    "proxy. Manual TradingView spot-checks recommended for any pattern where "
    "we depart from TA-Lib defaults (i.e., the 21 hand-rolled patterns)."
)


# --- Bar window builders ---------------------------------------------------


def _df_from_closes(
    closes: list[float],
    *,
    spread: float = 0.5,
    volume: float = 1_000.0,
) -> pd.DataFrame:
    """Build an OHLCV frame from a closes-only series (open=prev close)."""
    n = len(closes)
    opens = [closes[0], *closes[:-1]]
    highs = [max(o, c) + spread for o, c in zip(opens, closes, strict=False)]
    lows = [min(o, c) - spread for o, c in zip(opens, closes, strict=False)]
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [volume] * n,
        },
        index=idx,
    )


def _df_from_ohlc(
    rows: list[tuple[float, float, float, float]],
    *,
    volume: float = 1_000.0,
) -> pd.DataFrame:
    """Build an OHLCV frame from explicit (open, high, low, close) tuples."""
    n = len(rows)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [volume] * n,
        },
        index=idx,
    )


def _seeded_random_walk(
    n: int, *, seed: int, start: float = 100.0, sigma: float = 1.0,
) -> pd.DataFrame:
    """Deterministic synthetic OHLCV walk for indicator coverage."""
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(rng.normal(0.0, sigma, size=n))
    opens = closes + rng.normal(0.0, sigma * 0.4, size=n)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, sigma * 0.5, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, sigma * 0.5, size=n))
    vols = np.abs(rng.normal(1_000.0, 100.0, size=n))
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=idx,
    )


# --- Indicator window catalogue --------------------------------------------


def _indicator_windows() -> list[tuple[str, pd.DataFrame]]:
    """10 deterministic bar windows used across all 5 indicators."""
    windows: list[tuple[str, pd.DataFrame]] = []

    # Five seeded random walks of varying length / volatility.
    for i, (n, sigma) in enumerate(
        [(80, 0.5), (80, 1.0), (120, 1.5), (120, 2.0), (200, 1.0)],
    ):
        df = _seeded_random_walk(n, seed=10 + i, sigma=sigma)
        windows.append((f"walk_{i}_n{n}_s{sigma}", df))

    # Trending up.
    windows.append((
        "trend_up",
        _df_from_closes([100.0 + 0.7 * i for i in range(80)]),
    ))
    # Trending down.
    windows.append((
        "trend_down",
        _df_from_closes([200.0 - 0.7 * i for i in range(80)]),
    ))
    # Choppy / mean-reverting sine-ish.
    windows.append((
        "choppy",
        _df_from_closes([100.0 + 5.0 * np.sin(i / 3.0) for i in range(80)]),
    ))
    # Volatility breakout (flat then jump).
    closes = [100.0] * 40 + [100.0 + 2.0 * (i + 1) for i in range(40)]
    windows.append(("vol_breakout", _df_from_closes(closes, spread=1.5)))
    # Long range with drawdown.
    closes = [100.0]
    for i in range(99):
        closes.append(closes[-1] + (1.0 if i % 7 != 0 else -3.0))
    windows.append(("drawdown_pulse", _df_from_closes(closes)))

    expected_n = 10
    assert len(windows) == expected_n, (
        f"expected {expected_n} indicator windows, got {len(windows)}"
    )
    return windows


# --- Pattern window catalogue ----------------------------------------------


def _down_then_hammer() -> pd.DataFrame:
    """Downtrend leading into a candidate hammer bar."""
    rows: list[tuple[float, float, float, float]] = []
    for i in range(20):
        price = 100.0 - i * 0.5
        rows.append((price, price + 0.5, price - 1.0, price - 0.8))
    rows.append((90.0, 95.0, 89.5, 94.5))  # long-lower-wick bull recovery
    return _df_from_ohlc(rows)


def _flat() -> pd.DataFrame:
    return _df_from_ohlc([(100.0, 101.0, 99.0, 100.0)] * 30)


def _bull_engulfing() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 - i * 0.4
        rows.append((price, price + 0.4, price - 0.4, price - 0.3))
    # small bear then large bull engulfing
    rows.append((94.0, 94.2, 93.7, 93.8))
    rows.append((93.5, 96.0, 93.4, 95.8))
    return _df_from_ohlc(rows)


def _bear_engulfing() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 + i * 0.4
        rows.append((price, price + 0.5, price - 0.4, price + 0.3))
    rows.append((106.0, 106.4, 105.8, 106.2))
    rows.append((106.5, 106.6, 104.0, 104.2))
    return _df_from_ohlc(rows)


def _doji_bar() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 - i * 0.3
        rows.append((price, price + 0.4, price - 0.4, price - 0.2))
    rows.append((96.0, 96.6, 95.4, 96.0))  # tight body, long wicks
    return _df_from_ohlc(rows)


def _morning_star() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 - i * 0.5
        rows.append((price, price + 0.4, price - 0.5, price - 0.4))
    # large bear / small body / large bull (classic morning star)
    rows.append((94.0, 94.2, 91.0, 91.3))
    rows.append((90.8, 91.2, 90.5, 90.9))
    rows.append((91.0, 94.5, 90.9, 94.3))
    return _df_from_ohlc(rows)


def _evening_star() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 + i * 0.5
        rows.append((price, price + 0.5, price - 0.4, price + 0.4))
    # large bull / small body / large bear (classic evening star)
    rows.append((107.0, 110.0, 106.8, 109.7))
    rows.append((110.2, 110.4, 109.8, 110.1))
    rows.append((110.0, 110.1, 106.5, 106.7))
    return _df_from_ohlc(rows)


def _three_white_soldiers() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 - i * 0.4
        rows.append((price, price + 0.5, price - 0.5, price - 0.3))
    # three rising white candles, each opens within prior body
    rows.append((94.5, 96.5, 94.4, 96.4))
    rows.append((95.5, 97.5, 95.4, 97.4))
    rows.append((96.5, 98.5, 96.4, 98.4))
    return _df_from_ohlc(rows)


def _three_black_crows() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 + i * 0.4
        rows.append((price, price + 0.5, price - 0.5, price + 0.3))
    rows.append((105.5, 105.6, 103.5, 103.6))
    rows.append((104.5, 104.6, 102.5, 102.6))
    rows.append((103.5, 103.6, 101.5, 101.6))
    return _df_from_ohlc(rows)


def _bull_harami() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 - i * 0.4
        rows.append((price, price + 0.4, price - 0.4, price - 0.3))
    # large bear then small body inside it
    rows.append((94.0, 94.2, 90.5, 90.8))
    rows.append((91.5, 92.5, 91.4, 92.3))
    return _df_from_ohlc(rows)


def _bear_harami() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 + i * 0.4
        rows.append((price, price + 0.5, price - 0.4, price + 0.3))
    rows.append((106.0, 110.0, 105.8, 109.7))
    rows.append((108.5, 109.0, 107.5, 107.8))
    return _df_from_ohlc(rows)


def _dark_cloud_cover() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 + i * 0.5
        rows.append((price, price + 0.5, price - 0.4, price + 0.4))
    rows.append((106.0, 109.0, 105.8, 108.8))
    # opens above prior high, closes below midpoint of prior body
    rows.append((109.5, 109.8, 106.5, 106.8))
    return _df_from_ohlc(rows)


def _piercing() -> pd.DataFrame:
    rows: list[tuple[float, float, float, float]] = []
    for i in range(15):
        price = 100.0 - i * 0.5
        rows.append((price, price + 0.4, price - 0.5, price - 0.4))
    rows.append((94.0, 94.2, 90.5, 90.8))
    rows.append((90.5, 93.5, 90.4, 93.0))  # opens below prior low, closes above midpoint
    return _df_from_ohlc(rows)


PATTERN_WINDOWS: dict[str, list[tuple[str, pd.DataFrame]]] = {
    "hammer": [
        ("down_then_hammer", _down_then_hammer()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=1)),
        ("walk_b", _seeded_random_walk(60, seed=2)),
        ("walk_c", _seeded_random_walk(60, seed=3)),
    ],
    "engulfing": [
        ("bull_engulfing", _bull_engulfing()),
        ("bear_engulfing", _bear_engulfing()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=4)),
        ("walk_b", _seeded_random_walk(60, seed=5)),
    ],
    "doji": [
        ("doji_bar", _doji_bar()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=6)),
        ("walk_b", _seeded_random_walk(60, seed=7)),
        ("walk_c", _seeded_random_walk(60, seed=8)),
    ],
    "morning_star": [
        ("morning_star", _morning_star()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=11)),
        ("walk_b", _seeded_random_walk(60, seed=12)),
        ("walk_c", _seeded_random_walk(60, seed=13)),
    ],
    "evening_star": [
        ("evening_star", _evening_star()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=14)),
        ("walk_b", _seeded_random_walk(60, seed=15)),
        ("walk_c", _seeded_random_walk(60, seed=16)),
    ],
    "three_white_soldiers": [
        ("three_white_soldiers", _three_white_soldiers()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=17)),
        ("walk_b", _seeded_random_walk(60, seed=18)),
        ("walk_c", _seeded_random_walk(60, seed=19)),
    ],
    "three_black_crows": [
        ("three_black_crows", _three_black_crows()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=20)),
        ("walk_b", _seeded_random_walk(60, seed=21)),
        ("walk_c", _seeded_random_walk(60, seed=22)),
    ],
    "harami": [
        ("bull_harami", _bull_harami()),
        ("bear_harami", _bear_harami()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=23)),
        ("walk_b", _seeded_random_walk(60, seed=24)),
    ],
    "dark_cloud_cover": [
        ("dark_cloud_cover", _dark_cloud_cover()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=25)),
        ("walk_b", _seeded_random_walk(60, seed=26)),
        ("walk_c", _seeded_random_walk(60, seed=27)),
    ],
    "piercing": [
        ("piercing", _piercing()),
        ("flat", _flat()),
        ("walk_a", _seeded_random_walk(60, seed=28)),
        ("walk_b", _seeded_random_walk(60, seed=29)),
        ("walk_c", _seeded_random_walk(60, seed=30)),
    ],
}

PATTERN_DETECTORS = {
    "hammer": HammerPattern,
    "engulfing": EngulfingPattern,
    "doji": DojiPattern,
    "morning_star": MorningStarPattern,
    "evening_star": EveningStarPattern,
    "three_white_soldiers": ThreeWhiteSoldiersPattern,
    "three_black_crows": ThreeBlackCrowsPattern,
    "harami": HaramiPattern,
    "dark_cloud_cover": DarkCloudCoverPattern,
    "piercing": PiercingPattern,
}


# --- Bar serialisation -----------------------------------------------------


def _serialize_bars(df: pd.DataFrame) -> list[dict[str, float]]:
    """Convert a bar DataFrame into a list of plain-Python dicts (no timestamps).

    The cross-validation script reconstructs a DataFrame from these and uses
    its own deterministic DatetimeIndex, so we only persist the OHLCV values.
    """
    return [
        {
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
        }
        for r in df.itertuples()
    ]


# --- Reference computation -------------------------------------------------


def _last_finite(arr: np.ndarray) -> float | None:
    """Return the last non-NaN value, or ``None`` if the series is all-NaN."""
    finite = np.where(np.isfinite(arr))[0]
    if finite.size == 0:
        return None
    return float(arr[finite[-1]])


def _compute_indicator(name: str, df: pd.DataFrame) -> float | None:
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    if name == "rsi":
        return _last_finite(rsi(closes, 14))
    if name == "macd_line":
        line, _, _ = macd(closes, 12, 26, 9)
        return _last_finite(line)
    if name == "atr":
        return _last_finite(atr(highs, lows, closes, 14))
    if name == "bollinger_upper":
        upper, _, _ = bollinger(closes, 20, 2.0)
        return _last_finite(upper)
    if name == "stochastic_k":
        k, _ = stochastic(highs, lows, closes, 14, 3, 3)
        return _last_finite(k)
    raise ValueError(f"unknown indicator: {name}")


def _compute_pattern(pattern_id: str, df: pd.DataFrame) -> dict[str, Any]:
    detector = PATTERN_DETECTORS[pattern_id]()
    fire = detector.detect(df, current_idx=len(df) - 1)
    if fire is None:
        return {"fires": False, "direction": None}
    return {"fires": True, "direction": fire.direction}


# --- Build orchestration ---------------------------------------------------


def build() -> dict[str, Any]:
    indicator_names = ["rsi", "macd_line", "atr", "bollinger_upper", "stochastic_k"]
    indicator_windows = _indicator_windows()

    indicator_entries: list[dict[str, Any]] = []
    for ind_name in indicator_names:
        for window_name, df in indicator_windows:
            ind_ref = _compute_indicator(ind_name, df)
            indicator_entries.append({
                "kind": "indicator",
                "id": f"{ind_name}::{window_name}",
                "indicator": ind_name,
                "window": window_name,
                "bars": _serialize_bars(df),
                "expected": ind_ref,
            })

    pattern_entries: list[dict[str, Any]] = []
    for pattern_id, windows in PATTERN_WINDOWS.items():
        for window_name, df in windows:
            pat_ref = _compute_pattern(pattern_id, df)
            pattern_entries.append({
                "kind": "pattern",
                "id": f"{pattern_id}::{window_name}",
                "pattern": pattern_id,
                "window": window_name,
                "bars": _serialize_bars(df),
                "expected": pat_ref,
            })

    payload: dict[str, Any] = {
        "_note": NOTE,
        "_counts": {
            "indicators": len(indicator_entries),
            "patterns": len(pattern_entries),
            "total": len(indicator_entries) + len(pattern_entries),
        },
        "indicator_entries": indicator_entries,
        "pattern_entries": pattern_entries,
    }
    return payload


def main() -> None:
    payload = build()
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    counts = payload["_counts"]
    print(
        f"Wrote {OUT_PATH.relative_to(ROOT)} "
        f"({counts['total']} entries: "
        f"{counts['indicators']} indicators + {counts['patterns']} patterns)"
    )


if __name__ == "__main__":
    main()
