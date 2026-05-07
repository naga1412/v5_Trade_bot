"""SP-4 Phase C4 — bridge between predictor.py and the RL brain stack.

The predictor's per-tick flow is:

    layer scores L1..L9 → first aggregate (proposed direction) → traps →
    [BRAIN HOOK HERE] → final aggregate (with brain_adjust) → tier

This module's :func:`compute_brain_adjust_and_persist` is the BRAIN HOOK.
Returns the multiplier the predictor passes to the second
``aggregate(...)`` call. Exists in its own module so:

* the import surface in :mod:`app.core.predictor` stays small,
* the brain integration can be unit-tested independently of the
  predictor's giant per-tick orchestration, and
* graceful degradation (no checkpoint loaded → ``brain_adjust=1.0``) is
  centralised in one place.

Module-state caveat: the per-asset embedding table lives at module
scope here so the predictor doesn't have to plumb it through. Tests
can clear it via :func:`reset_glue_state`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.rl.adapter import AssetEmbeddingTable
from app.rl.audit import record_brain_decision
from app.rl.checkpoints import get_active_policy_and_checkpoint
from app.rl.inference import (
    BrainDecision,
    action_to_brain_adjust,
    decide_action,
)
from app.rl.obs import (
    MacroFeatures,
    MarketFeatures,
    PositionState,
)


log = logging.getLogger(__name__)


_asset_table: AssetEmbeddingTable | None = None


def _get_asset_table() -> AssetEmbeddingTable:
    """Lazy-init the per-process AssetEmbeddingTable.

    Held at module scope so the predictor (which has no instance state)
    can call this on every tick without allocating. The table starts
    empty; symbols register on first decide_action() call per spec sec
    Q5 cold-start blending.
    """
    global _asset_table
    if _asset_table is None:
        _asset_table = AssetEmbeddingTable()
    return _asset_table


def reset_glue_state() -> None:
    """Test hook: drop the cached embedding table."""
    global _asset_table
    _asset_table = None


@dataclass(frozen=True)
class BrainHookResult:
    """What the predictor needs back from the brain hook."""

    brain_adjust: float
    decision: BrainDecision | None  # None when no active checkpoint


async def compute_brain_adjust_and_persist(
    *,
    symbol: str,
    proposed_direction: str,
    layer_scores: tuple[float, ...],
    market: MarketFeatures,
    position: PositionState,
    macro: MacroFeatures,
    session: AsyncSession | None,
) -> BrainHookResult:
    """Run brain inference + persist the decision; return the multiplier.

    On any failure path (no checkpoint loaded, obs build error, forward
    pass error, persistence error), returns ``brain_adjust=1.0`` —
    aggregator will multiply by 1.0 and the predictor's behaviour is
    indistinguishable from the pre-SP-4 equal-weight path.

    ``session`` may be ``None`` (legacy callers); in that case we skip
    the ``brain_decisions`` write but still consume the inference for
    the multiplier. This matches the pattern SP-9 used for L9 news.
    """
    if get_active_policy_and_checkpoint() is None:
        # Fast path — no checkpoint loaded. Pre-SP-4 behaviour.
        return BrainHookResult(brain_adjust=1.0, decision=None)

    table = _get_asset_table()
    asset_id = table.register_asset(symbol)

    try:
        decision = decide_action(
            asset_id=asset_id,
            symbol=symbol,
            asset_table=table,
            layer_scores=layer_scores,
            market=market,
            position=position,
            macro=macro,
        )
    except Exception:  # noqa: BLE001 — never break the predictor
        log.warning("brain_glue: decide_action raised; falling back to no-op",
                    exc_info=True)
        return BrainHookResult(brain_adjust=1.0, decision=None)

    if decision is None:
        return BrainHookResult(brain_adjust=1.0, decision=None)

    brain_adjust = action_to_brain_adjust(
        decision.smoothed_action, proposed_direction=proposed_direction,
    )

    if session is not None:
        try:
            await record_brain_decision(
                session,
                decision=decision,
                symbol=symbol,
                observation=_serialise_inputs(layer_scores, market, position, macro),
            )
        except Exception:  # noqa: BLE001 — audit failure must not block trading
            log.warning(
                "brain_glue: record_brain_decision failed; "
                "trading continues but audit row missing",
                exc_info=True,
            )

    return BrainHookResult(brain_adjust=brain_adjust, decision=decision)


def _serialise_inputs(
    layer_scores: tuple[float, ...],
    market: MarketFeatures,
    position: PositionState,
    macro: MacroFeatures,
) -> list[float]:
    """Compact input serialisation for the brain_decisions.observation column.

    Stores the SAME 26-ish values used to build the 58-float observation
    (sans embedding, which is reproducible from the asset_id), so a
    future replay can reconstruct the obs deterministically:

      L1..L9 (9) + market 5 num + regime 1 string + position 3 + macro 4
      = 22 floats + 1 string

    For Phase C we keep the layout simple — full canonical obs storage
    is a follow-up if it turns out to matter for brain_decisions audit.
    """
    out: list[Any] = list(layer_scores)
    out.extend([
        market.atr_pct, market.funding_rate, market.oi_delta_24h,
        market.dxy_corr_30d, market.gold_corr_30d, market.regime,
    ])
    out.extend([
        float(position.cur_position),
        position.unrealized_pnl_R,
        float(position.bars_in_position),
    ])
    out.extend([
        macro.hours_to_next_high_impact,
        float(macro.fomc_window),
        float(macro.weekend),
        float(macro.asia_open),
    ])
    return out  # type: ignore[return-value]


__all__ = [
    "BrainHookResult",
    "compute_brain_adjust_and_persist",
    "reset_glue_state",
]
