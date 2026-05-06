import asyncio

import numpy as np
import pandas as pd

from app.core.predictor import build_prediction
from app.core.scoring.layer2_patterns import PatternStatsLookup


def _run(coro):
    """Sync bridge for the SP-9 async build_prediction.

    All existing test cases (1300+) call build_prediction synchronously;
    SP-9 Phase E2 made it async without passing a session (L9 still
    abstains). Wrap the coroutine in asyncio.run so test bodies can stay
    plain ``def`` until SP-9 Phase F migrates them.
    """
    return asyncio.run(coro)


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
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    assert pred.symbol == "BTC/USDT"
    assert pred.timeframe == "1h"
    assert pred.final.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -1.0 <= pred.final.score <= 1.0
    assert "1" in pred.layer_scores  # Layer 1 always evaluated
    assert pred.inputs_hash  # non-empty


def test_uptrend_yields_long_direction() -> None:
    bars = make_bars(250)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    assert pred.final.direction == "LONG"


def test_inputs_hash_is_deterministic_for_same_input() -> None:
    bars = make_bars()
    a = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    b = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    assert a.inputs_hash == b.inputs_hash


def test_layer2_score_is_none_when_no_lookup_passed() -> None:
    """Backward-compat: callers that don't pass pattern_stats_lookup keep L2 unset."""
    bars = make_bars()
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    assert pred.layer_scores["2"] is None


def test_layer2_score_runs_when_lookup_passed() -> None:
    """SP-2 Phase E E3: passing a PatternStatsLookup runs L2 and lands a score."""
    bars = make_bars()
    lookup = PatternStatsLookup(by_pattern={})
    pred = _run(build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars,
        pattern_stats_lookup=lookup,
    ))
    layer2 = pred.layer_scores["2"]
    assert layer2 is not None
    assert layer2.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert 0.0 <= layer2.strength <= 1.0
    assert 0.0 <= layer2.confidence <= 1.0


def test_layer2_score_respects_disabled_patterns() -> None:
    """Empty `enabled_patterns` set ⇒ no patterns can fire ⇒ NEUTRAL with strength 0."""
    bars = make_bars()
    lookup = PatternStatsLookup(by_pattern={})
    pred = _run(build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars,
        pattern_stats_lookup=lookup,
        enabled_patterns=set(),
    ))
    layer2 = pred.layer_scores["2"]
    assert layer2 is not None
    assert layer2.direction == "NEUTRAL"
    assert layer2.strength == 0.0


# --- SP-5 Phase F1: full 10-layer + traps + tier wiring ----------------------


def test_build_prediction_includes_all_ten_layer_slots() -> None:
    """All 10 layer slots are present (some may be None — placeholders)."""
    bars = make_bars(300)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    for i in range(1, 11):
        assert str(i) in pred.layer_scores


def test_build_prediction_exposes_extras_payload_for_persistence() -> None:
    """SP-5 Phase F1: ``prediction_extras`` carries tier + trap fires + raw scores
    for the JSONB persistence site to merge into ``predictions.layer_scores``.
    """
    bars = make_bars(300)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    extras = pred.prediction_extras
    assert extras is not None
    # Required fields per Phase F1 spec.
    assert "traps_fired" in extras
    assert isinstance(extras["traps_fired"], list)
    assert "static_score" in extras
    assert "brain_adjust" in extras and extras["brain_adjust"] == 1.0
    assert "trap_factor" in extras and 0.0 < extras["trap_factor"] <= 1.0
    assert "news_multiplier" in extras and extras["news_multiplier"] == 1.0
    assert "direction_penalty" in extras
    assert "final" in extras
    assert "tier" in extras
    assert extras["tier"] in {"NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+"}


def test_build_prediction_layer4_smc_runs_when_enough_bars() -> None:
    bars = make_bars(300)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    # L4 needs >= 60 bars; with 300 bars on a clean uptrend, it should produce
    # a non-None LayerScore (direction may vary).
    assert pred.layer_scores["4"] is not None


def test_build_prediction_layer6_micro_runs() -> None:
    bars = make_bars(300)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    # L6 always returns a score for non-empty bars.
    assert pred.layer_scores["6"] is not None


def test_build_prediction_placeholder_layers_are_none() -> None:
    """L7 (XGBoost), L9 (news, no session), L10 (brain) abstain → None."""
    bars = make_bars(300)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    assert pred.layer_scores["7"] is None
    assert pred.layer_scores["9"] is None
    assert pred.layer_scores["10"] is None


def test_build_prediction_layer8_is_none_without_ghost() -> None:
    bars = make_bars(300)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    assert pred.layer_scores["8"] is None


def test_build_prediction_layer8_runs_with_ghost_input() -> None:
    """Pass a GhostInput so L8 fires."""
    from app.core.scoring.layer8_convlstm import GhostInput

    bars = make_bars(300)
    last = float(bars["close"].iloc[-1])
    ghost = GhostInput(ghost_close=last * 1.02, ghost_uncertainty=0.2)
    pred = _run(build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars, ghost=ghost,
    ))
    assert pred.layer_scores["8"] is not None


def test_build_prediction_extras_tier_matches_final_score() -> None:
    """The tier in extras matches what classify_tier(final) returns."""
    from app.api.schemas import FinalScoreOut
    from app.core.scoring.tiers import classify_tier
    from app.core.scoring.types import Direction, FinalScore

    bars = make_bars(300)
    pred = _run(build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars))
    extras = pred.prediction_extras
    assert extras is not None

    # Reconstruct a FinalScore matching the persisted final + direction
    # to verify the tier derivation is consistent.
    assert isinstance(pred.final, FinalScoreOut)
    final_obj = FinalScore(
        score=pred.final.score,
        direction=Direction(pred.final.direction),
        confidence=pred.final.confidence,
        layer_results={},
    )
    assert extras["tier"] == classify_tier(final_obj)
