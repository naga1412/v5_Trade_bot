"""Tests for app.rl.obs.build_observation (SP-4 Phase A2)."""
from __future__ import annotations

import numpy as np
import pytest

from app.rl.obs import (
    EMB_DIM,
    N_LAYER_SCORES,
    OBS_DIM,
    REGIME_NAMES,
    AssetState,
    MacroFeatures,
    MarketFeatures,
    PositionState,
    build_observation,
    encode_regime,
)


def _market(regime: str = "bull_breakout") -> MarketFeatures:
    return MarketFeatures(
        atr_pct=0.02,
        funding_rate=0.0001,
        oi_delta_24h=0.05,
        regime=regime,
    )


def _position(cur: int = 0) -> PositionState:
    return PositionState(cur_position=cur, unrealized_pnl_R=0.0, bars_in_position=0)


def _macro() -> MacroFeatures:
    return MacroFeatures(
        weekend=False,
        asia_open=True,
    )


def test_observation_dimension_constants_match_spec() -> None:
    """PR-OBS-DIM-REDUCE: 32 (emb) + 9 (layers) + 8 (market) + 3 (pos) + 2 (macro) = 54.

    market = 3 numeric (ATR%, funding, OI Δ24h) + 5 regime one-hot.
    dxy_corr_30d, gold_corr_30d, hours_to_next_high_impact, fomc_window were
    dropped 2026-08-05 — zero live collector ever, permanent hardcoded
    constants in every observation ever captured.
    """
    assert OBS_DIM == 54
    assert EMB_DIM == 32
    assert N_LAYER_SCORES == 9
    assert len(REGIME_NAMES) == 5


def test_observation_has_correct_shape_and_dtype() -> None:
    asset = AssetState(asset_id=0, embedding=np.zeros(32, dtype=np.float32))
    layer_scores = (0.1, -0.2, 0.3, 0.0, 0.5, -0.1, 0.2, 0.4, -0.3)
    obs = build_observation(asset, layer_scores, _market(), _position(), _macro())
    assert obs.shape == (OBS_DIM,)
    assert obs.dtype == np.float32


def test_observation_packs_components_in_documented_order() -> None:
    """Embedding first, then layers, then market, then regime one-hot, then
    position, then macro — verify by inspecting slice ranges."""
    asset = AssetState(
        asset_id=0,
        embedding=np.full(32, 7.0, dtype=np.float32),
    )
    layers = tuple(range(1, 10))  # 1, 2, 3, ..., 9
    obs = build_observation(asset, layers, _market(), _position(), _macro())
    # First 32 floats are the embedding (all 7.0)
    assert np.allclose(obs[:32], 7.0)
    # Next 9 floats are the layer scores 1..9
    assert np.allclose(obs[32:41], np.arange(1, 10, dtype=np.float32))
    # Then 3 numeric market features (ATR%, funding, OI Δ24h)
    assert np.allclose(
        obs[41:44], [0.02, 0.0001, 0.05], atol=1e-6,
    )
    # Then 5 floats for the regime one-hot
    assert obs[44:49].sum() == 1.0
    # Then 3 position floats (cur=0, unrealized=0, bars=0)
    assert np.allclose(obs[49:52], [0.0, 0.0, 0.0])
    # Then 2 macro floats (weekend=0, asia=1)
    assert np.allclose(obs[52:54], [0.0, 1.0])


def test_layer_scores_must_be_exactly_nine() -> None:
    asset = AssetState(asset_id=0, embedding=np.zeros(32, dtype=np.float32))
    with pytest.raises(ValueError, match="9 layer scores"):
        build_observation(asset, (0.1,) * 8, _market(), _position(), _macro())
    with pytest.raises(ValueError, match="9 layer scores"):
        build_observation(asset, (0.1,) * 10, _market(), _position(), _macro())


def test_embedding_must_be_thirty_two_dim() -> None:
    asset = AssetState(asset_id=0, embedding=np.zeros(16, dtype=np.float32))
    with pytest.raises(ValueError, match="32-dim"):
        build_observation(asset, (0.0,) * 9, _market(), _position(), _macro())


def test_regime_one_hot_unique_per_name() -> None:
    """All 5 regime names map to a unique one-hot index in [0,4]."""
    seen = set()
    for r in REGIME_NAMES:
        v = encode_regime(r)
        assert v.shape == (5,)
        assert v.sum() == 1.0
        seen.add(int(np.argmax(v)))
    assert seen == {0, 1, 2, 3, 4}


def test_unknown_regime_falls_back_to_zero_vector() -> None:
    v = encode_regime("alien_regime")
    assert v.shape == (5,)
    assert v.sum() == 0.0


def test_position_signed_to_match_action_space() -> None:
    """cur_position should reach -1.0 / +1.0 in the obs vector."""
    asset = AssetState(asset_id=0, embedding=np.zeros(32, dtype=np.float32))
    short = build_observation(
        asset, (0.0,) * 9, _market(), _position(cur=-1), _macro(),
    )
    long_ = build_observation(
        asset, (0.0,) * 9, _market(), _position(cur=+1), _macro(),
    )
    assert short[49] == -1.0
    assert long_[49] == +1.0


def test_observation_is_deterministic_for_same_inputs() -> None:
    asset = AssetState(asset_id=7, embedding=np.arange(32, dtype=np.float32) / 100)
    args = (
        asset,
        (0.1, 0.2, -0.1, 0.0, 0.4, -0.3, 0.5, -0.2, 0.0),
        _market(),
        _position(),
        _macro(),
    )
    a = build_observation(*args)
    b = build_observation(*args)
    assert np.array_equal(a, b)
