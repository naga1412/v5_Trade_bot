"""SP-4 Phase C2 — production inference for the RL brain.

Spec sec 6 — every closed candle, after layer scores L1..L9 are
computed, the predictor calls :func:`decide_action` here to ask the
brain what action it would take given the current observation.

This module is **pure read** of the active checkpoint loaded by
:mod:`app.rl.checkpoints` at backend startup; no DB writes happen
here. Persistence to ``brain_decisions`` is the caller's job (see
:mod:`app.rl.audit`).

Graceful degradation: if no checkpoint is active (the common case
until SP-4 ships its first trained brain), :func:`decide_action`
returns ``None`` and the predictor falls back to equal-weight
aggregation per spec sec 6.1.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

import numpy as np
import torch

from app.config import get_settings
from app.rl.adapter import AssetEmbeddingTable
from app.rl.checkpoints import (
    get_active_policy_and_checkpoint,
)
from app.rl.obs import (
    AssetState,
    MacroFeatures,
    MarketFeatures,
    PositionState,
    build_observation,
)
from app.rl.replay_buffer import ALL_ACTIONS
from app.rl.safety import SMOOTHING_WINDOW, smooth_action


log = logging.getLogger(__name__)


# Per-asset deque of recent raw actions; bounded at SMOOTHING_WINDOW.
# Module-level state because the predictor is stateless across ticks
# but smoothing needs to remember 2-3 priors per asset.
_history_by_symbol: dict[str, deque[str]] = {}


@dataclass(frozen=True)
class BrainDecision:
    """Result of one brain inference call.

    ``raw_action`` is what the policy emitted; ``smoothed_action`` is
    what should actually be acted on (passes through the α=0.3 mode
    smoother). ``logits`` and ``value_estimate`` are persisted to
    ``brain_decisions`` for replay debugging.
    """

    raw_action: str
    smoothed_action: str
    logits: list[float]
    value_estimate: float
    checkpoint_id: int
    checkpoint_version: str


def _history_for(symbol: str) -> deque[str]:
    """Get-or-create the bounded deque for this symbol's smoothing state."""
    h = _history_by_symbol.get(symbol)
    if h is None:
        h = deque(maxlen=SMOOTHING_WINDOW)
        _history_by_symbol[symbol] = h
    return h


def reset_smoothing_state() -> None:
    """Test hook: drop all per-asset smoothing history."""
    _history_by_symbol.clear()


def decide_action(
    *,
    asset_id: int,
    symbol: str,
    asset_table: AssetEmbeddingTable,
    layer_scores: tuple[float, ...],
    market: MarketFeatures,
    position: PositionState,
    macro: MacroFeatures,
    n_trades_for_asset: int = 0,
) -> BrainDecision | None:
    """Run brain inference for one closed candle on ``symbol``.

    Returns a :class:`BrainDecision` if a checkpoint is active and the
    forward pass succeeded; ``None`` otherwise (caller falls back to
    equal-weight aggregation in that case).

    Steps:
      1. Look up the active (policy, checkpoint) pair from module state.
      2. Get the (cold-start-blended) embedding for ``symbol`` via the
         supplied :class:`AssetEmbeddingTable`.
      3. Build the 58-float observation via
         :func:`app.rl.obs.build_observation`.
      4. Run a deterministic forward (model.eval, ``deterministic=True``)
         and pull (action_idx, log_prob, value).
      5. Smooth via :func:`app.rl.safety.smooth_action` against the
         per-asset history.
      6. Append the raw action to history for the next tick.
    """
    pair = get_active_policy_and_checkpoint()
    if pair is None:
        return None
    policy, ck = pair

    embedding = asset_table.get_embedding(
        symbol, n_trades_for_asset=n_trades_for_asset,
    )
    asset_state = AssetState(asset_id=asset_id, embedding=embedding)
    try:
        obs = build_observation(asset_state, layer_scores, market, position, macro)
    except ValueError as e:
        log.warning("decide_action: obs build failed for %s: %s", symbol, e)
        return None

    obs_t = torch.from_numpy(np.expand_dims(obs, 0))
    try:
        with torch.no_grad():
            policy.eval()
            action_t, _log_prob_t, value_t = policy.act(
                obs_t, deterministic=True,
            )
            policy_out = policy.forward(obs_t)
            logits = policy_out.logits.squeeze(0).cpu().numpy().astype(float).tolist()
    except Exception as e:  # noqa: BLE001 — never let inference break the predictor
        log.error("decide_action: forward pass failed for %s: %s", symbol, e)
        return None

    action_idx = int(action_t.squeeze(0).item())
    if not 0 <= action_idx < len(ALL_ACTIONS):
        log.error(
            "decide_action: out-of-range action %d for %s; ignoring",
            action_idx, symbol,
        )
        return None
    raw_action = ALL_ACTIONS[action_idx]

    history = _history_for(symbol)
    smoothed = smooth_action(history, raw_action)
    history.append(raw_action)

    return BrainDecision(
        raw_action=raw_action,
        smoothed_action=smoothed,
        logits=logits,
        value_estimate=float(value_t.squeeze(0).item()),
        checkpoint_id=ck.id,
        checkpoint_version=ck.version,
    )


def action_to_brain_adjust(
    smoothed_action: str, *, proposed_direction: str,
) -> float:
    """Map a brain action + the L1..L9 proposed direction to a brain_adjust.

    PR-BRAIN-SOFTER-ACTIONS (2026-05-22) parameterised the mapping on a
    single ``SPREAD`` knob (``settings.BRAIN_ACTION_MULTIPLIER_SPREAD``,
    default 0.15). The mapping is symmetric around 1.0 with linear
    half-steps:

      FLAT       → 1 − SPREAD          (full-strength suppress)
      disagree   → 1 − SPREAD / 2      (half-strength suppress)
      NEUTRAL    → 1.0                 (no-op)
      agree-half → 1 + SPREAD / 2      (half-strength boost)
      agree-full → 1 + SPREAD          (full-strength boost)

    Lower SPREAD = brain is more conservative (less influence per
    decision); higher SPREAD = brain has more sway. v1 default 0.15
    chosen post-PR-BRAIN-BACKTEST-PHASEB5 to reduce blast radius of
    bad brain decisions while id=3-class checkpoints accumulate
    evidence; raise as the brain demonstrates consistent positive
    Sharpe.

    The aggregator (``app.core.scoring.aggregator``) bounds-checks the
    return value to ``(0, 2)`` exclusive — guaranteed satisfied for any
    SPREAD < 1.0.

    ``proposed_direction`` is one of ``"LONG"`` / ``"SHORT"`` / ``"NEUTRAL"``
    matching ``app.core.scoring.types.Direction``.
    """
    spread = get_settings().BRAIN_ACTION_MULTIPLIER_SPREAD

    if smoothed_action == "FLAT":
        return 1.0 - spread

    is_brain_long = smoothed_action.startswith("LONG_")
    is_brain_short = smoothed_action.startswith("SHORT_")
    is_brain_full = smoothed_action.endswith("_FULL")

    if proposed_direction == "LONG" and is_brain_long:
        return (1.0 + spread) if is_brain_full else (1.0 + spread / 2)
    if proposed_direction == "SHORT" and is_brain_short:
        return (1.0 + spread) if is_brain_full else (1.0 + spread / 2)

    if proposed_direction == "NEUTRAL":
        # L1..L9 abstained but brain says trade; conservative: keep
        # neutral (multiplier doesn't matter when score is ~0). 1.0
        # is a no-op and preserves the static aggregator's verdict.
        return 1.0

    # Brain disagrees with proposed (proposed=LONG, brain=SHORT or vice versa).
    return 1.0 - spread / 2


__all__ = [
    "BrainDecision",
    "action_to_brain_adjust",
    "decide_action",
    "reset_smoothing_state",
]
