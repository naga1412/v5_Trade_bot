"""PR-BRAIN-BACKTEST-PHASEB5 — chronological-holdout brain backtest."""
from __future__ import annotations

import numpy as np
import pytest
import torch

from app.rl.adapter import AssetEmbeddingTable
from app.rl.backtest_brain import (
    MIN_HOLDOUT_TRADES,
    BacktestResult,
    _compute_max_drawdown,
    _compute_sharpe,
    _infer_proposed_direction,
    evaluate_brain_on_holdout,
)
from app.rl.obs import OBS_DIM
from app.rl.policy import PolicyNetwork
from app.rl.replay_buffer import (
    ACTION_LONG_FULL,
    ACTION_SHORT_FULL,
    Transition,
)


def _asset_table() -> AssetEmbeddingTable:
    """PR-BRAIN-EMBEDDINGS-LEARNABLE made evaluate_brain_on_holdout require
    an asset_table kwarg (to read live, trained embeddings via hot-swap)."""
    t = AssetEmbeddingTable()
    t.bulk_register(["BTCUSDT"])
    return t


def _make_transition(
    *,
    reward: float,
    action: str = ACTION_LONG_FULL,
    symbol: str = "BTCUSDT",
    asset_id: int = 0,
    opened_at_iso: str = "2026-05-14T17:00:00+00:00",
) -> Transition:
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    return Transition(
        obs=obs,
        action=action,
        reward=reward,
        next_obs=obs.copy(),
        done=True,
        asset_id=asset_id,
        symbol=symbol,
        opened_at_iso=opened_at_iso,
    )


def test_infer_proposed_direction_handles_all_actions() -> None:
    assert _infer_proposed_direction("LONG_FULL") == "LONG"
    assert _infer_proposed_direction("LONG_HALF") == "LONG"
    assert _infer_proposed_direction("SHORT_FULL") == "SHORT"
    assert _infer_proposed_direction("SHORT_HALF") == "SHORT"
    assert _infer_proposed_direction("FLAT") == "NEUTRAL"
    assert _infer_proposed_direction("garbage") == "NEUTRAL"


def test_compute_sharpe_zero_variance_safe() -> None:
    """Constant-reward series → mean/(0+eps) → finite, no NaN."""
    rewards = np.ones(50, dtype=np.float64)
    sharpe = _compute_sharpe(rewards)
    assert not np.isnan(sharpe)
    assert sharpe > 0  # constant positive → positive Sharpe (very large)


def test_compute_sharpe_empty_returns_zero() -> None:
    assert _compute_sharpe(np.array([], dtype=np.float64)) == 0.0


def test_compute_max_drawdown_simple_case() -> None:
    """Rewards [+1, +1, -3] → cumsum [+1, +2, -1] → peak [1, 2, 2] → dd max = 3."""
    rewards = np.array([1.0, 1.0, -3.0], dtype=np.float64)
    assert _compute_max_drawdown(rewards) == pytest.approx(3.0)


def test_compute_max_drawdown_no_drawdown() -> None:
    """Monotonic-positive returns → 0 drawdown."""
    rewards = np.array([0.1, 0.2, 0.3], dtype=np.float64)
    assert _compute_max_drawdown(rewards) == pytest.approx(0.0)


def test_compute_max_drawdown_empty_returns_zero() -> None:
    assert _compute_max_drawdown(np.array([], dtype=np.float64)) == 0.0


def test_evaluate_returns_insufficient_data_when_below_threshold() -> None:
    """< MIN_HOLDOUT_TRADES holdout → sentinel dict, not BacktestResult."""
    policy = PolicyNetwork()
    # 50 transitions × 20% = 10, well below MIN_HOLDOUT_TRADES (20).
    transitions = [_make_transition(reward=0.1) for _ in range(50)]
    out = evaluate_brain_on_holdout(
        policy=policy, asset_table=_asset_table(), transitions=transitions,
    )
    assert isinstance(out, dict)
    assert out["_status"] == "insufficient_data"
    assert out["min_required"] == MIN_HOLDOUT_TRADES
    assert out["had"] == 10
    assert out["total_transitions"] == 50


def test_evaluate_returns_backtest_result_when_above_threshold() -> None:
    """≥ MIN_HOLDOUT_TRADES holdout → BacktestResult with finite metrics."""
    torch.manual_seed(7)  # determinism — policy.act samples are deterministic in eval+argmax mode
    policy = PolicyNetwork()
    # 120 transitions × 20% = 24 holdout, above the 20 threshold.
    transitions = [
        _make_transition(
            reward=0.05 if i % 2 == 0 else -0.05,
            opened_at_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00+00:00",
        )
        for i in range(120)
    ]

    out = evaluate_brain_on_holdout(policy=policy, asset_table=_asset_table(), transitions=transitions)

    assert isinstance(out, BacktestResult)
    assert out.total_trades == 24
    assert np.isfinite(out.sharpe)
    assert np.isfinite(out.max_dd)
    assert 0.0 <= out.win_rate <= 1.0
    assert out.window_start != ""
    assert out.window_end != ""


def test_evaluate_uses_chronological_split() -> None:
    """Holdout window must be the LAST 20%, not a random slice."""
    torch.manual_seed(7)
    policy = PolicyNetwork()

    transitions = []
    for i in range(120):
        # opened_at increments monotonically so we can verify the boundary
        transitions.append(
            _make_transition(
                reward=float(i),
                opened_at_iso=f"2026-05-22T{(i % 24):02d}:{(i % 60):02d}:00+00:00",
            ),
        )

    out = evaluate_brain_on_holdout(policy=policy, asset_table=_asset_table(), transitions=transitions)
    assert isinstance(out, BacktestResult)
    # Holdout starts at int(120 * 0.8) = 96, ends at 119.
    expected_window_start = transitions[96].opened_at_iso
    expected_window_end = transitions[-1].opened_at_iso
    assert out.window_start == expected_window_start
    assert out.window_end == expected_window_end


def test_evaluate_handles_short_action_transitions() -> None:
    """SHORT_FULL transitions get proposed_direction='SHORT' → distinct
    brain_adjust mapping from LONG_FULL."""
    torch.manual_seed(7)
    policy = PolicyNetwork()
    transitions = [
        _make_transition(
            reward=0.1,
            action=ACTION_SHORT_FULL,
            opened_at_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00+00:00",
        )
        for i in range(120)
    ]
    out = evaluate_brain_on_holdout(policy=policy, asset_table=_asset_table(), transitions=transitions)
    # Just assert we get a valid result — the inference path covers SHORT too.
    assert isinstance(out, BacktestResult)
    assert out.total_trades == 24


def test_evaluate_preserves_policy_train_mode() -> None:
    """The backtest must restore policy.training=True after running,
    so a caller that resumes training isn't silently in eval mode."""
    policy = PolicyNetwork()
    policy.train()  # ensure we're in training mode
    assert policy.training is True

    transitions = [
        _make_transition(
            reward=0.1,
            opened_at_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00+00:00",
        )
        for i in range(120)
    ]
    evaluate_brain_on_holdout(policy=policy, asset_table=_asset_table(), transitions=transitions)

    assert policy.training is True, (
        "policy.training was flipped to False — caller-state leak from "
        "policy.eval() inside evaluate_brain_on_holdout"
    )


def test_evaluate_preserves_policy_eval_mode() -> None:
    """And vice versa — a caller in eval mode stays in eval mode."""
    policy = PolicyNetwork()
    policy.eval()
    assert policy.training is False

    transitions = [
        _make_transition(
            reward=0.1,
            opened_at_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00+00:00",
        )
        for i in range(120)
    ]
    evaluate_brain_on_holdout(policy=policy, asset_table=_asset_table(), transitions=transitions)

    assert policy.training is False


def test_evaluate_writes_per_trade_sidecar_when_path_given(tmp_path) -> None:
    """PR-BRAIN-COLD-START-PROBE — per_trade_log_path argument writes a
    JSON sidecar with one record per holdout trade, capturing brain
    action + top-3 logits + outcome for misclass audit."""
    import json
    import torch
    torch.manual_seed(7)
    policy = PolicyNetwork()
    transitions = [
        _make_transition(
            reward=0.05 if i % 2 == 0 else -0.05,
            opened_at_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00+00:00",
        )
        for i in range(120)
    ]
    sidecar = tmp_path / "per_trade.json"

    result = evaluate_brain_on_holdout(
        policy=policy, asset_table=_asset_table(),
        transitions=transitions, per_trade_log_path=sidecar,
    )

    assert isinstance(result, BacktestResult)
    assert sidecar.exists(), "sidecar JSON not written"
    records = json.loads(sidecar.read_text())
    assert len(records) == result.total_trades
    # Schema check on first record
    r0 = records[0]
    expected_keys = {
        "symbol", "asset_id", "opened_at", "recorded_action",
        "recorded_direction", "brain_action", "brain_top3_logits",
        "brain_adjust", "raw_reward", "synthetic_reward", "outcome",
    }
    assert expected_keys.issubset(r0.keys())
    # top3 should have exactly 3 entries each with action + logit
    assert len(r0["brain_top3_logits"]) == 3
    for entry in r0["brain_top3_logits"]:
        assert "action" in entry and "logit" in entry
    assert r0["outcome"] in {"WIN", "LOSS", "FLAT"}


def test_evaluate_does_not_write_sidecar_when_path_none(tmp_path) -> None:
    """Default behavior: no sidecar path → no file written.

    Backward-compat: existing callers without per_trade_log_path keep
    working with no extra I/O.
    """
    import torch
    torch.manual_seed(7)
    policy = PolicyNetwork()
    transitions = [
        _make_transition(
            reward=0.05,
            opened_at_iso=f"2026-05-{(i % 28) + 1:02d}T12:00:00+00:00",
        )
        for i in range(120)
    ]

    nonexistent = tmp_path / "should_not_be_created.json"
    evaluate_brain_on_holdout(
        policy=policy, asset_table=_asset_table(),
        transitions=transitions,
        # per_trade_log_path defaults to None
    )
    assert not nonexistent.exists()


def test_backtest_result_as_eval_dict_round_trips() -> None:
    """The serialised dict has exactly the 6 expected keys with float/int types."""
    result = BacktestResult(
        sharpe=1.23, max_dd=0.45, win_rate=0.55, total_trades=42,
        window_start="2026-05-01T00:00:00+00:00",
        window_end="2026-05-22T00:00:00+00:00",
    )
    d = result.as_eval_dict()
    assert set(d.keys()) == {
        "sharpe", "max_dd", "win_rate", "total_trades",
        "window_start", "window_end",
    }
    assert d["sharpe"] == 1.23
    assert d["total_trades"] == 42
