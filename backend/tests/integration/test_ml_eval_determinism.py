"""Integration test: eval harness must be deterministic across all 5 regimes (SP-1 B5).

Spec sec 5.3 / sec 13 — given the same model, bars, window, and seed, the eval
harness must return byte-identical MAE. This guards against accidental
randomness leaking from torch/numpy global state into the per-bar prediction
loop.

This is the integration variant of B2's per-window determinism test — runs
against the *whole* RandomWalkBaseline x REGIME_WINDOWS matrix to guarantee
no randomness leaks across all 5 windows simultaneously.
"""
import numpy as np
import pandas as pd

from app.ml.baseline import RandomWalkBaseline
from app.ml.eval import evaluate_on_regime
from app.ml.regimes import REGIME_WINDOWS


def _synthetic_bars_for(window) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    n_hours = int((window.end - window.start).total_seconds() / 3600) + 1
    rng = np.random.default_rng(seed=hash(window.name) & 0xFFFF)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, size=n_hours)))
    df = pd.DataFrame({
        "open":  closes,
        "high":  closes * 1.001,
        "low":   closes * 0.999,
        "close": closes,
        "volume": np.full(n_hours, 1000.0),
    })
    df.index = pd.date_range(start=window.start, periods=n_hours, freq="1h", tz="UTC")
    return df


def test_all_five_windows_evaluate_deterministically() -> None:
    model = RandomWalkBaseline()
    first_run: dict[str, float] = {}
    for window in REGIME_WINDOWS:
        bars = _synthetic_bars_for(window)
        r = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
        first_run[window.name] = r.mae

    second_run: dict[str, float] = {}
    for window in REGIME_WINDOWS:
        bars = _synthetic_bars_for(window)
        r = evaluate_on_regime(model=model, bars=bars, window=window, seed=42)
        second_run[window.name] = r.mae

    assert first_run == second_run
    assert len(first_run) == 5
