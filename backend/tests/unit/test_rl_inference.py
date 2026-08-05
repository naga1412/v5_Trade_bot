"""Tests for app.rl.inference (SP-4 Phase C2)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import torch

from app.config import get_settings
from app.rl.adapter import AssetEmbeddingTable
from app.rl.checkpoints import (
    ActiveRlCheckpoint,
    clear_active,
    set_active,
)
from app.rl.inference import (
    BrainDecision,
    action_to_brain_adjust,
    decide_action,
    reset_smoothing_state,
)
from app.rl.obs import (
    MacroFeatures,
    MarketFeatures,
    PositionState,
)
from app.rl.policy import PolicyNetwork
from app.rl.replay_buffer import (
    ACTION_FLAT,
    ACTION_LONG_FULL,
    ACTION_LONG_HALF,
    ACTION_SHORT_FULL,
    ACTION_SHORT_HALF,
)


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    clear_active()
    reset_smoothing_state()


def _market() -> MarketFeatures:
    return MarketFeatures(
        atr_pct=0.02, funding_rate=0.0001, oi_delta_24h=0.05,
        regime="bull_breakout",
    )


def _position() -> PositionState:
    return PositionState(cur_position=0, unrealized_pnl_R=0.0, bars_in_position=0)


def _macro() -> MacroFeatures:
    return MacroFeatures(weekend=False, asia_open=False)


def _layers() -> tuple[float, ...]:
    return tuple(0.1 * i for i in range(1, 10))


def _activate_policy_returning_action(action_idx: int) -> PolicyNetwork:
    """Pin a policy whose argmax always returns ``action_idx``."""
    policy = PolicyNetwork()
    with torch.no_grad():
        policy.policy_head.weight.zero_()
        policy.policy_head.bias.zero_()
        policy.policy_head.bias[action_idx] = 10.0
    ck = ActiveRlCheckpoint(
        id=42, model_name="ppo_policy_v1", version="v1-test",
        sha256="0" * 64, checkpoint_uri="file:///x.pt",
    )
    set_active(policy, ck)
    return policy


# ---- decide_action ------------------------------------------------------


def test_no_active_checkpoint_returns_none() -> None:
    """When no policy is loaded, decide_action falls back gracefully."""
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")
    out = decide_action(
        asset_id=0, symbol="BTC/USDT", asset_table=table,
        layer_scores=_layers(),
        market=_market(), position=_position(), macro=_macro(),
    )
    assert out is None


def test_active_checkpoint_returns_decision() -> None:
    _activate_policy_returning_action(0)  # LONG_FULL
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")

    out = decide_action(
        asset_id=0, symbol="BTC/USDT", asset_table=table,
        layer_scores=_layers(),
        market=_market(), position=_position(), macro=_macro(),
    )
    assert isinstance(out, BrainDecision)
    assert out.raw_action == ACTION_LONG_FULL
    assert out.smoothed_action == ACTION_LONG_FULL
    assert len(out.logits) == 5
    assert isinstance(out.value_estimate, float)
    assert out.checkpoint_id == 42
    assert out.checkpoint_version == "v1-test"


def test_decide_action_smooths_across_calls() -> None:
    """3-call sequence with the same forced action produces the same smoothed
    output (history and raw match)."""
    _activate_policy_returning_action(2)  # FLAT
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")

    decisions = []
    for _ in range(3):
        out = decide_action(
            asset_id=0, symbol="BTC/USDT", asset_table=table,
            layer_scores=_layers(),
            market=_market(), position=_position(), macro=_macro(),
        )
        decisions.append(out)
    assert all(d.raw_action == ACTION_FLAT for d in decisions)
    assert all(d.smoothed_action == ACTION_FLAT for d in decisions)


def test_decide_action_per_symbol_history_is_isolated() -> None:
    """Different symbols don't share smoothing state."""
    _activate_policy_returning_action(0)  # LONG_FULL
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")
    table.register_asset("ETH/USDT")

    btc_dec = decide_action(
        asset_id=0, symbol="BTC/USDT", asset_table=table,
        layer_scores=_layers(),
        market=_market(), position=_position(), macro=_macro(),
    )
    eth_dec = decide_action(
        asset_id=1, symbol="ETH/USDT", asset_table=table,
        layer_scores=_layers(),
        market=_market(), position=_position(), macro=_macro(),
    )
    assert btc_dec is not None and eth_dec is not None
    # Both first-tick → smoothed equals raw (history was empty).
    assert btc_dec.raw_action == btc_dec.smoothed_action
    assert eth_dec.raw_action == eth_dec.smoothed_action


def test_obs_build_failure_returns_none() -> None:
    """If layer_scores has wrong length, decide_action gracefully returns None."""
    _activate_policy_returning_action(0)
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")

    out = decide_action(
        asset_id=0, symbol="BTC/USDT", asset_table=table,
        layer_scores=(0.1, 0.2),  # only 2 — should be 9
        market=_market(), position=_position(), macro=_macro(),
    )
    assert out is None


def test_forward_pass_failure_returns_none() -> None:
    """If the policy raises during inference, fall back to None."""
    policy = _activate_policy_returning_action(0)
    table = AssetEmbeddingTable()
    table.register_asset("BTC/USDT")

    with patch.object(policy, "act", side_effect=RuntimeError("simulated")):
        out = decide_action(
            asset_id=0, symbol="BTC/USDT", asset_table=table,
            layer_scores=_layers(),
            market=_market(), position=_position(), macro=_macro(),
        )
    assert out is None


# ---- action_to_brain_adjust ---------------------------------------------
#
# PR-BRAIN-SOFTER-ACTIONS replaced the hardcoded {0.5, 0.6, 1.0, 1.2, 1.4}
# mapping with a single tunable SPREAD parameter (default 0.15).
# Tests assert against the formula instead of literal values.


def _spread() -> float:
    """Read the current default spread from settings."""
    return get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD


def test_brain_long_full_agrees_with_proposed_long_boosts() -> None:
    assert action_to_brain_adjust(
        ACTION_LONG_FULL, proposed_direction="LONG",
    ) == pytest.approx(1.0 + _spread())


def test_brain_long_half_agrees_with_proposed_long_smaller_boost() -> None:
    assert action_to_brain_adjust(
        ACTION_LONG_HALF, proposed_direction="LONG",
    ) == pytest.approx(1.0 + _spread() / 2)


def test_brain_short_full_agrees_with_proposed_short_boosts() -> None:
    assert action_to_brain_adjust(
        ACTION_SHORT_FULL, proposed_direction="SHORT",
    ) == pytest.approx(1.0 + _spread())


def test_brain_short_half_agrees_with_proposed_short_smaller_boost() -> None:
    assert action_to_brain_adjust(
        ACTION_SHORT_HALF, proposed_direction="SHORT",
    ) == pytest.approx(1.0 + _spread() / 2)


def test_brain_flat_suppresses() -> None:
    expected = 1.0 - _spread()
    for direction in ("LONG", "SHORT", "NEUTRAL"):
        assert action_to_brain_adjust(
            ACTION_FLAT, proposed_direction=direction,
        ) == pytest.approx(expected)


def test_brain_disagrees_with_proposed_long_suppresses() -> None:
    expected = 1.0 - _spread() / 2
    assert action_to_brain_adjust(
        ACTION_SHORT_FULL, proposed_direction="LONG",
    ) == pytest.approx(expected)
    assert action_to_brain_adjust(
        ACTION_SHORT_HALF, proposed_direction="LONG",
    ) == pytest.approx(expected)


def test_brain_disagrees_with_proposed_short_suppresses() -> None:
    expected = 1.0 - _spread() / 2
    assert action_to_brain_adjust(
        ACTION_LONG_FULL, proposed_direction="SHORT",
    ) == pytest.approx(expected)
    assert action_to_brain_adjust(
        ACTION_LONG_HALF, proposed_direction="SHORT",
    ) == pytest.approx(expected)


def test_brain_action_when_proposed_neutral_no_op() -> None:
    """When L1..L9 abstain, brain_adjust = 1.0 (no-op) — multiplier on a
    near-zero score doesn't change the verdict."""
    for action in (ACTION_LONG_FULL, ACTION_LONG_HALF,
                   ACTION_SHORT_FULL, ACTION_SHORT_HALF):
        assert action_to_brain_adjust(action, proposed_direction="NEUTRAL") == 1.0


def test_brain_adjust_stays_in_aggregator_open_interval() -> None:
    """Aggregator validates brain_adjust ∈ (0, 2). Every mapped value
    must respect that bound — defensively check all 5 actions × 3
    directions produce a number strictly in that range."""
    for action in (ACTION_LONG_FULL, ACTION_LONG_HALF, ACTION_FLAT,
                   ACTION_SHORT_HALF, ACTION_SHORT_FULL):
        for direction in ("LONG", "SHORT", "NEUTRAL"):
            adj = action_to_brain_adjust(action, proposed_direction=direction)
            assert 0.0 < adj < 2.0


# ---- PR-BRAIN-SOFTER-ACTIONS: spread parametrization tests -------------


def test_brain_action_multiplier_spread_default_is_conservative() -> None:
    """Default spread chosen post-PR-BACKTEST-PHASEB5 to reduce blast
    radius. Pinning the value catches accidental retunes."""
    assert get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD == 0.15


def test_action_to_brain_adjust_respects_spread_override(monkeypatch) -> None:
    """When SPREAD is monkeypatched on the cached Settings instance, the
    mapping shifts symmetrically.

    With SPREAD=0.4, the legacy-ish mapping {0.6, 0.8, 1.0, 1.2, 1.4}
    emerges — confirms backward-compat path via env/settings override.
    """
    monkeypatch.setattr(
        get_settings(), "BRAIN_ACTION_MULTIPLIER_SPREAD", 0.4,
    )
    assert action_to_brain_adjust(
        ACTION_FLAT, proposed_direction="LONG",
    ) == pytest.approx(0.6)  # 1 - 0.4
    assert action_to_brain_adjust(
        ACTION_SHORT_FULL, proposed_direction="LONG",
    ) == pytest.approx(0.8)  # 1 - 0.4/2 (disagree)
    assert action_to_brain_adjust(
        ACTION_LONG_HALF, proposed_direction="LONG",
    ) == pytest.approx(1.2)  # 1 + 0.4/2
    assert action_to_brain_adjust(
        ACTION_LONG_FULL, proposed_direction="LONG",
    ) == pytest.approx(1.4)  # 1 + 0.4


@pytest.mark.parametrize("spread", [0.05, 0.15, 0.4, 0.7, 0.95])
def test_action_to_brain_adjust_remains_in_aggregator_bounds_under_spread_sweep(
    monkeypatch, spread: float,
) -> None:
    """Aggregator's (0, 2) exclusive bound must hold for any plausible
    SPREAD ∈ (0, 1). Sweep catches off-by-one bound errors."""
    monkeypatch.setattr(
        get_settings(), "BRAIN_ACTION_MULTIPLIER_SPREAD", spread,
    )
    for action in (ACTION_LONG_FULL, ACTION_LONG_HALF,
                   ACTION_FLAT, ACTION_SHORT_HALF, ACTION_SHORT_FULL):
        for direction in ("LONG", "SHORT", "NEUTRAL"):
            adj = action_to_brain_adjust(
                action, proposed_direction=direction,
            )
            assert 0.0 < adj < 2.0, (
                f"spread={spread} action={action} "
                f"dir={direction} → adj={adj} out of bounds"
            )
