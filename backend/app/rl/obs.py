"""Observation builder for the SP-4 PPO policy (L10).

Assembles a 54-float vector that the policy network sees on each
prediction tick:

    asset_embedding (32)
  + layer_scores L1..L9 (9)
  + market_state (8: ATR%, funding, OI Δ24h, regime one-hot[5])
  + position_state (3: cur_pos {-1,0,+1}, unrealized_pnl_R, bars_in_position)
  + macro_calendar (2: weekend, asia_open)
  = 54

PR-OBS-DIM-REDUCE (2026-08-05): dropped 4 dims that had zero live collector
and were never going to be populated without new infrastructure —
dxy_corr_30d, gold_corr_30d (no FX/gold feed exists), hours_to_next_high_impact,
fomc_window (no econ-calendar source wired). All four were permanent
hardcoded constants (0.0 / 24.0 / False) for every observation ever
captured — pure dead weight, not signal. Per
[[observation_vector_audit_2026_08_04]]: remove now rather than backfill
later, since populating fabricated historical values for a feature live
still can't see would manufacture train/serve skew. This is an OBS_DIM
change — incompatible with every prior checkpoint, forces a from-scratch
retrain (operator-accepted tradeoff).

The exact same function runs at training-time (replay buffer
construction) and inference-time (production) — that's the spec sec 8
cross-cutting policy on training-serving skew.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np


OBS_DIM: int = 54
EMB_DIM: int = 32
N_LAYER_SCORES: int = 9

# Frozen — same names as the SP-1 regimes table so eval/training joins
# don't need a translation layer. KEEP IN SYNC with app.ml.regimes.
REGIME_NAMES: tuple[str, ...] = (
    "bull_breakout",
    "bear_crash",
    "sideways_grind",
    "high_volatility",
    "low_volatility",
)


RegimeName = Literal[
    "bull_breakout",
    "bear_crash",
    "sideways_grind",
    "high_volatility",
    "low_volatility",
]


@dataclass(frozen=True)
class AssetState:
    """Per-asset state — id (universe row PK) + the 32-dim learned embedding."""

    asset_id: int
    embedding: np.ndarray  # shape (32,), float32


@dataclass(frozen=True)
class MarketFeatures:
    """3 numeric market features + 1 regime label.

    `regime` should be one of REGIME_NAMES; unknown labels gracefully
    degrade to a zero one-hot so the brain at least sees the rest of the
    observation.
    """

    atr_pct: float
    funding_rate: float
    oi_delta_24h: float
    regime: str


@dataclass(frozen=True)
class PositionState:
    cur_position: int        # {-1, 0, +1}
    unrealized_pnl_R: float
    bars_in_position: int


@dataclass(frozen=True)
class MacroFeatures:
    weekend: bool
    asia_open: bool


def encode_regime(name: str) -> np.ndarray:
    """5-vector one-hot encoding of a regime name; unknown → zero vector.

    Returning zeros (not raising) on unknown is intentional — the brain
    keeps inferring on the rest of the observation if a misconfigured
    regime label sneaks through. Logged at the call-site, not here.
    """
    out = np.zeros(5, dtype=np.float32)
    if name in REGIME_NAMES:
        out[REGIME_NAMES.index(name)] = 1.0
    return out


def build_observation(
    asset: AssetState,
    layer_scores: Sequence[float],
    market: MarketFeatures,
    position: PositionState,
    macro: MacroFeatures,
) -> np.ndarray:
    """Assemble a single (54,) float32 observation. See module docstring."""
    if len(layer_scores) != N_LAYER_SCORES:
        raise ValueError(
            f"expected 9 layer scores (L1..L9), got {len(layer_scores)}"
        )
    if asset.embedding.shape != (EMB_DIM,):
        raise ValueError(
            f"expected 32-dim asset embedding, got shape "
            f"{tuple(asset.embedding.shape)}"
        )

    parts: list[np.ndarray] = [
        asset.embedding.astype(np.float32),
        np.asarray(layer_scores, dtype=np.float32),
        np.array([
            market.atr_pct,
            market.funding_rate,
            market.oi_delta_24h,
        ], dtype=np.float32),
        encode_regime(market.regime),
        np.array([
            float(position.cur_position),
            float(position.unrealized_pnl_R),
            float(position.bars_in_position),
        ], dtype=np.float32),
        np.array([
            float(macro.weekend),
            float(macro.asia_open),
        ], dtype=np.float32),
    ]
    obs = np.concatenate(parts)
    if obs.shape != (OBS_DIM,):  # pragma: no cover — invariant check
        raise RuntimeError(
            f"obs assembly bug: got shape {obs.shape}, expected ({OBS_DIM},)"
        )
    return obs


__all__ = [
    "OBS_DIM",
    "EMB_DIM",
    "N_LAYER_SCORES",
    "REGIME_NAMES",
    "AssetState",
    "MarketFeatures",
    "PositionState",
    "MacroFeatures",
    "RegimeName",
    "encode_regime",
    "build_observation",
]
