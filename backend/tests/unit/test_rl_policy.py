"""Tests for app.rl.policy.PolicyNetwork (SP-4 Phase B1)."""
from __future__ import annotations

import pytest
import torch

from app.rl.obs import OBS_DIM
from app.rl.policy import (
    HIDDEN_DIM,
    N_ACTIONS,
    PolicyNetwork,
    PolicyOutput,
    count_params,
)


def test_constants_match_spec() -> None:
    assert N_ACTIONS == 5
    assert HIDDEN_DIM == 128


def test_forward_shapes() -> None:
    net = PolicyNetwork()
    obs = torch.zeros(8, OBS_DIM)
    out = net(obs)
    assert isinstance(out, PolicyOutput)
    assert out.logits.shape == (8, N_ACTIONS)
    assert out.value.shape == (8,)


def test_forward_rejects_wrong_obs_dim() -> None:
    net = PolicyNetwork()
    with pytest.raises(ValueError, match="expected obs shape"):
        net(torch.zeros(8, OBS_DIM - 1))


def test_forward_rejects_unbatched_input() -> None:
    net = PolicyNetwork()
    with pytest.raises(ValueError, match="expected obs shape"):
        net(torch.zeros(OBS_DIM))  # missing batch dimension


def test_act_stochastic_returns_action_in_range() -> None:
    torch.manual_seed(0)
    net = PolicyNetwork()
    obs = torch.zeros(16, OBS_DIM)
    action, log_prob, value = net.act(obs, deterministic=False)
    assert action.shape == (16,)
    assert log_prob.shape == (16,)
    assert value.shape == (16,)
    assert action.min().item() >= 0
    assert action.max().item() < N_ACTIONS


def test_act_deterministic_picks_argmax() -> None:
    net = PolicyNetwork()
    # Force a known logit pattern — argmax should be class 2.
    with torch.no_grad():
        net.policy_head.weight.zero_()
        net.policy_head.bias.zero_()
        net.policy_head.bias[2] = 5.0
    obs = torch.zeros(4, OBS_DIM)
    action, _, _ = net.act(obs, deterministic=True)
    assert (action == 2).all()


def test_evaluate_actions_returns_scoring_tuple() -> None:
    net = PolicyNetwork()
    obs = torch.zeros(4, OBS_DIM)
    actions = torch.tensor([0, 1, 2, 3])
    log_probs, entropy, values = net.evaluate_actions(obs, actions)
    assert log_probs.shape == (4,)
    assert entropy.shape == (4,)
    assert values.shape == (4,)
    # Categorical entropy on uniform-ish logits is positive.
    assert (entropy > 0).all()


def test_initial_logits_are_near_uniform() -> None:
    """Spec sec 5.2 — entropy bonus does the job; init policy near-uniform.

    The orthogonal init with gain=0.01 on the policy head means the
    initial logits are tiny, so softmax produces nearly-uniform action
    distribution — desired for PPO warm-up.
    """
    torch.manual_seed(42)
    net = PolicyNetwork()
    obs = torch.zeros(1, OBS_DIM)
    out = net(obs)
    probs = torch.softmax(out.logits, dim=-1).squeeze(0)
    # Each prob should be near 1/5 = 0.2; allow 5% slack on either side.
    for p in probs:
        assert 0.15 <= float(p) <= 0.25


def test_param_count_in_expected_range() -> None:
    """~50K params is what spec sec 3.3 quotes; allow some slack."""
    net = PolicyNetwork()
    n = count_params(net)
    # 53*128 + 128*128 + 128*5 + 128*1 + biases ≈ 24K
    # Spec quoted "~50K" for the policy + asset embedding combined.
    # Pure policy without embedding is closer to ~25K.
    assert 10_000 <= n <= 60_000


def test_state_dict_round_trip() -> None:
    a = PolicyNetwork()
    with torch.no_grad():
        a.policy_head.bias[2] = 1.234
    b = PolicyNetwork()
    b.load_state_dict(a.state_dict())
    assert torch.allclose(a.policy_head.bias, b.policy_head.bias)


def test_train_mode_dropout_is_active() -> None:
    net = PolicyNetwork(dropout=0.5)
    net.train()
    torch.manual_seed(0)
    obs = torch.ones(64, OBS_DIM)
    h1 = net.shared(obs)
    h2 = net.shared(obs)
    # In train mode, dropout's stochastic mask makes successive forwards differ.
    assert not torch.equal(h1, h2)


def test_eval_mode_is_deterministic() -> None:
    net = PolicyNetwork(dropout=0.5)
    net.eval()
    obs = torch.ones(64, OBS_DIM)
    h1 = net.shared(obs)
    h2 = net.shared(obs)
    assert torch.equal(h1, h2)
