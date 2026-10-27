import numpy as np
import pandas as pd

from app.core.predictor import build_prediction
from app.core.scoring.layer2_patterns import PatternStatsLookup

import pytest

# ---------------------------------------------------------------------------
# GRANDFATHERED NETWORK ACCESS -- PRE-EXISTING DEBT, NOT APPROVAL.
# ---------------------------------------------------------------------------
# This module reaches the real internet. It was already doing so before the
# suite-wide egress block landed (2026-10-26); the block did not create this
# dependency, it REVEALED it. 51 tests across 10 files were found this way,
# all routing through the real build_prediction path, which fetches live
# market data.
#
# WHY MARKED RATHER THAN FIXED HERE: mocking the transport would change what
# each test actually verifies, across ten files, and that is its own piece
# of work with its own review. Marking makes the dependency EXPLICIT AND
# ENUMERATED instead of invisible, and keeps the guard live for every other
# test -- which is the point: a NEW test that reaches the network now fails
# immediately instead of waiting to be noticed.
#
# PARTITIONED 2026-10-27, BEFORE anyone pays this down: 51 STRUCTURE,
# 0 VALUE. Every one of the 51 asserts on fixture-injected values (seeded
# shadow_trades rows, monkeypatched ghost candles, seeded PatternStatsLookup,
# seeded news with a FROZEN clock) or on a quantity's definitional range.
# Live data is an INPUT to these tests but cannot change any verdict, so
# there is NO false-green risk from market movement -- which is the thing
# that would have made this a project rather than a chore.
#
# Cross-checked against CI history: ZERO flakes among the 51 across the last
# 80 runs (4 failures total -- 2 from the commit that introduced the egress
# guard, 2 from TimescaleDB service-container startup, none a test in this
# set).
#
# WHAT IT STILL COSTS, so nobody rediscovers it: a fetch failure fails the
# test, so these cannot run in a sandboxed or offline runner, and CI runners
# reach Binance from SHARED GitHub IP RANGES -- meaning the suite depends on
# an external service's tolerance of OTHER TENANTS' traffic. The failure mode
# is not "our calls are too frequent", it is a 418/429 earned by somebody
# else. That is why the debt is real rather than cosmetic, even at 0 VALUE.
#
# Low risk to pay down: swap the fetch for a fixture and the assertions are
# unchanged. Removing a file from this list is the goal. Adding one is not.
# NOTE: marks are PER-TEST below, not module-level. A blanket
# pytestmark here granted network to 99 tests when 51 need it --
# the grant must be no broader than the evidence.


def make_bars(n: int = 250) -> pd.DataFrame:
    closes = list(np.linspace(100.0, 200.0, n))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes, "high": [c * 1.01 for c in closes],
        "low":  [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


# All tests here are `async def` so pytest-asyncio (auto mode) drives the loop
# natively. The previous sync-def + asyncio.run() bridge interacted badly with
# pytest-asyncio's loop-policy management and surfaced as a deterministic hang
# in the next async test file (test_ratelimit) during SP-9 CI runs.


@pytest.mark.allow_network
async def test_build_prediction_returns_required_keys() -> None:
    bars = make_bars()
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.symbol == "BTC/USDT"
    assert pred.timeframe == "1h"
    assert pred.final.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -1.0 <= pred.final.score <= 1.0
    assert "1" in pred.layer_scores  # Layer 1 always evaluated
    assert pred.inputs_hash  # non-empty


@pytest.mark.allow_network
async def test_uptrend_yields_long_direction() -> None:
    bars = make_bars(250)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.final.direction == "LONG"


@pytest.mark.allow_network
async def test_inputs_hash_is_deterministic_for_same_input() -> None:
    bars = make_bars()
    a = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    b = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert a.inputs_hash == b.inputs_hash


@pytest.mark.allow_network
async def test_layer2_score_is_none_when_no_lookup_passed() -> None:
    """Backward-compat: callers that don't pass pattern_stats_lookup keep L2 unset."""
    bars = make_bars()
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.layer_scores["2"] is None


@pytest.mark.allow_network
async def test_layer2_score_runs_when_lookup_passed() -> None:
    """SP-2 Phase E E3: passing a PatternStatsLookup runs L2 and lands a score."""
    bars = make_bars()
    lookup = PatternStatsLookup(by_pattern={})
    pred = await build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars,
        pattern_stats_lookup=lookup,
    )
    layer2 = pred.layer_scores["2"]
    assert layer2 is not None
    assert layer2.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert 0.0 <= layer2.strength <= 1.0
    assert 0.0 <= layer2.confidence <= 1.0


@pytest.mark.allow_network
async def test_layer2_score_respects_disabled_patterns() -> None:
    """Empty `enabled_patterns` set ⇒ no patterns can fire ⇒ NEUTRAL with strength 0."""
    bars = make_bars()
    lookup = PatternStatsLookup(by_pattern={})
    pred = await build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars,
        pattern_stats_lookup=lookup,
        enabled_patterns=set(),
    )
    layer2 = pred.layer_scores["2"]
    assert layer2 is not None
    assert layer2.direction == "NEUTRAL"
    assert layer2.strength == 0.0


# --- SP-5 Phase F1: full 10-layer + traps + tier wiring ----------------------


@pytest.mark.allow_network
async def test_build_prediction_includes_all_ten_layer_slots() -> None:
    """All 10 layer slots are present (some may be None — placeholders)."""
    bars = make_bars(300)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    for i in range(1, 11):
        assert str(i) in pred.layer_scores


@pytest.mark.allow_network
async def test_build_prediction_exposes_extras_payload_for_persistence() -> None:
    """SP-5 Phase F1: ``prediction_extras`` carries tier + trap fires + raw scores
    for the JSONB persistence site to merge into ``predictions.layer_scores``.
    """
    bars = make_bars(300)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    extras = pred.prediction_extras
    assert extras is not None
    # Required fields per Phase F1 spec.
    assert "traps_fired" in extras
    assert isinstance(extras["traps_fired"], list)
    assert "static_score" in extras
    assert "brain_adjust" in extras and isinstance(extras["brain_adjust"], float)
    assert "trap_factor" in extras and 0.0 < extras["trap_factor"] <= 1.0
    assert "news_multiplier" in extras and extras["news_multiplier"] == 1.0
    assert "direction_penalty" in extras
    assert "final" in extras
    assert "tier" in extras
    assert extras["tier"] in {"NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+"}


@pytest.mark.allow_network
async def test_build_prediction_layer4_smc_runs_when_enough_bars() -> None:
    bars = make_bars(300)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    # L4 needs >= 60 bars; with 300 bars on a clean uptrend, it should produce
    # a non-None LayerScore (direction may vary).
    assert pred.layer_scores["4"] is not None


@pytest.mark.allow_network
async def test_build_prediction_layer6_micro_runs() -> None:
    bars = make_bars(300)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    # L6 always returns a score for non-empty bars.
    assert pred.layer_scores["6"] is not None


@pytest.mark.allow_network
async def test_build_prediction_placeholder_layers_are_none() -> None:
    """L7 (XGBoost), L9 (news, no session), L10 (brain) abstain → None."""
    bars = make_bars(300)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.layer_scores["7"] is None
    assert pred.layer_scores["9"] is None
    assert pred.layer_scores["10"] is None


@pytest.mark.allow_network
async def test_build_prediction_layer8_is_none_without_ghost() -> None:
    bars = make_bars(300)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.layer_scores["8"] is None


@pytest.mark.allow_network
async def test_build_prediction_layer8_runs_with_ghost_input() -> None:
    """Pass a GhostInput so L8 fires."""
    from app.core.scoring.layer8_convlstm import GhostInput

    bars = make_bars(300)
    last = float(bars["close"].iloc[-1])
    ghost = GhostInput(ghost_close=last * 1.02, ghost_uncertainty=0.2)
    pred = await build_prediction(
        symbol="BTC/USDT", timeframe="1h", bars=bars, ghost=ghost,
    )
    assert pred.layer_scores["8"] is not None


@pytest.mark.allow_network
async def test_build_prediction_extras_tier_matches_final_score() -> None:
    """The tier in extras matches what classify_tier(final) returns."""
    from app.api.schemas import FinalScoreOut
    from app.core.scoring.tiers import classify_tier
    from app.core.scoring.types import Direction, FinalScore

    bars = make_bars(300)
    pred = await build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
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


@pytest.mark.allow_network
async def test_flow_features_do_not_reach_scoring() -> None:
    """W4 promotion scope guard (2026-08-06): flow_features (ls_account_ratio,
    taker_buy_sell_ratio, oi_4h_delta, oi_24h_delta) is collection-only.
    extras["features"] is a persistence-only bag never read by aggregate()
    or any of the L1-L9 layers — this proves it by forcing two wildly
    different flow_features._FLOW_CACHE states (all-None vs extreme
    non-None values) through the exact same bars/symbol and asserting the
    scoring output (final score, direction, confidence, every layer_scores
    entry) is bit-identical, while extras["features"] itself genuinely
    differs — so the mock actually took effect and only the record-only
    path saw it.
    """
    import app.core.features.flow_features as flow_mod

    symbol = "BTCUSDT"
    bars = make_bars(300)

    flow_mod._clear_cache_for_tests()
    pred_empty = await build_prediction(symbol=symbol, timeframe="1h", bars=bars)

    flow_mod._FLOW_CACHE[symbol] = {
        "ls_account_ratio": 0.99,
        "taker_buy_sell_ratio": 0.01,
        "oi_4h_delta": 5.0,
        "oi_24h_delta": -5.0,
    }
    try:
        pred_extreme = await build_prediction(symbol=symbol, timeframe="1h", bars=bars)
    finally:
        flow_mod._clear_cache_for_tests()

    # Scoring is bit-identical regardless of flow_features state.
    assert pred_empty.final.score == pred_extreme.final.score
    assert pred_empty.final.direction == pred_extreme.final.direction
    assert pred_empty.final.confidence == pred_extreme.final.confidence
    for layer_id in [str(i) for i in range(1, 11)]:
        a = pred_empty.layer_scores.get(layer_id)
        b = pred_extreme.layer_scores.get(layer_id)
        assert (a is None) == (b is None)
        if a is not None:
            assert a.model_dump() == b.model_dump()

    # But the record-only features bag genuinely differs -- proving the
    # mock reached persistence and the null result above isn't just
    # because flow_features was never consulted at all.
    empty_features = pred_empty.prediction_extras["features"]
    extreme_features = pred_extreme.prediction_extras["features"]
    assert empty_features["oi_4h_delta"] is None
    assert extreme_features["oi_4h_delta"] == 5.0
    assert empty_features != extreme_features
