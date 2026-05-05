"""SP-2 Phase F2 — cross-validation runner.

Loads ``sp2_reference.json`` (built by :mod:`tools.validation.build_sp2_reference`)
and re-runs the indicator / pattern computation with the *current* code.

Comparison rules (per the plan, §"F2"):
  * Indicators: absolute relative diff <= 0.001 (0.1%). When the expected value
    is ``None`` we require the recomputed value to also be ``None`` (the
    indicator is in its leading-NaN warmup window).
  * Patterns: exact ``fires`` boolean + ``direction`` string match.

Exit codes:
  * 0 — all 100 entries match.
  * 1 — at least one mismatch (a detailed report is printed to stdout).

Run from the worktree root::

    PYTHONPATH=backend python tools/validation/sp2_cross_check.py
"""
from __future__ import annotations

import json
import math
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

REFERENCE_PATH = Path(__file__).resolve().parent / "sp2_reference.json"
INDICATOR_TOLERANCE = 0.001  # 0.1 % relative; absolute fallback for tiny values

PATTERN_DETECTORS: dict[str, type] = {
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


def _bars_from_payload(bars_payload: list[dict[str, float]]) -> pd.DataFrame:
    n = len(bars_payload)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": [b["open"] for b in bars_payload],
            "high": [b["high"] for b in bars_payload],
            "low": [b["low"] for b in bars_payload],
            "close": [b["close"] for b in bars_payload],
            "volume": [b["volume"] for b in bars_payload],
        },
        index=idx,
    )


def _last_finite(arr: np.ndarray) -> float | None:
    finite = np.where(np.isfinite(arr))[0]
    if finite.size == 0:
        return None
    return float(arr[finite[-1]])


def _recompute_indicator(name: str, df: pd.DataFrame) -> float | None:
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


def _recompute_pattern(pattern_id: str, df: pd.DataFrame) -> dict[str, Any]:
    detector = PATTERN_DETECTORS[pattern_id]()
    fire = detector.detect(df, current_idx=len(df) - 1)
    if fire is None:
        return {"fires": False, "direction": None}
    return {"fires": True, "direction": fire.direction}


def _indicators_match(expected: float | None, actual: float | None) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    if math.isnan(expected) and math.isnan(actual):
        return True
    if math.isnan(expected) or math.isnan(actual):
        return False
    denom = max(abs(expected), 1.0)  # absolute fallback for very small refs
    return abs(expected - actual) / denom <= INDICATOR_TOLERANCE


def _patterns_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return (
        bool(expected.get("fires")) is bool(actual.get("fires"))
        and expected.get("direction") == actual.get("direction")
    )


def main() -> int:
    if not REFERENCE_PATH.exists():
        print(f"reference file not found: {REFERENCE_PATH}", file=sys.stderr)
        return 1

    payload = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    indicator_entries = payload["indicator_entries"]
    pattern_entries = payload["pattern_entries"]

    failures: list[dict[str, Any]] = []
    n_pass = 0
    n_total = 0

    for entry in indicator_entries:
        n_total += 1
        df = _bars_from_payload(entry["bars"])
        ind_actual = _recompute_indicator(entry["indicator"], df)
        ind_expected = entry["expected"]
        if _indicators_match(ind_expected, ind_actual):
            n_pass += 1
        else:
            failures.append({
                "kind": "indicator",
                "id": entry["id"],
                "expected": ind_expected,
                "actual": ind_actual,
            })

    for entry in pattern_entries:
        n_total += 1
        df = _bars_from_payload(entry["bars"])
        pat_actual = _recompute_pattern(entry["pattern"], df)
        pat_expected = entry["expected"]
        if _patterns_match(pat_expected, pat_actual):
            n_pass += 1
        else:
            failures.append({
                "kind": "pattern",
                "id": entry["id"],
                "expected": pat_expected,
                "actual": pat_actual,
            })

    print(f"sp2_cross_check: {n_pass}/{n_total} entries match")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(json.dumps(f, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
