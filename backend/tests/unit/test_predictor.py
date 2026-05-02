import numpy as np
import pandas as pd

from app.core.predictor import build_prediction


def make_bars(n: int = 250) -> pd.DataFrame:
    closes = list(np.linspace(100.0, 200.0, n))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes, "high": [c * 1.01 for c in closes],
        "low":  [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_build_prediction_returns_required_keys() -> None:
    bars = make_bars()
    pred = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.symbol == "BTC/USDT"
    assert pred.timeframe == "1h"
    assert pred.final.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -1.0 <= pred.final.score <= 1.0
    assert "1" in pred.layer_scores  # Layer 1 always evaluated
    assert pred.inputs_hash  # non-empty


def test_uptrend_yields_long_direction() -> None:
    bars = make_bars(250)
    pred = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.final.direction == "LONG"


def test_inputs_hash_is_deterministic_for_same_input() -> None:
    bars = make_bars()
    a = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    b = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert a.inputs_hash == b.inputs_hash
