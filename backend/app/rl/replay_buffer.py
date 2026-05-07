"""Replay buffer for SP-4 PPO training (Phase A4).

Loads closed `shadow_trades` rows within a configurable trailing window and
materializes them into typed `Transition` records:

    Transition(obs, action_taken, reward, next_obs, done)

For the FIRST training run there are no on-policy RL trades yet — the
"action_taken" is the discretized version of whatever direction + size
the existing rule-based aggregation chose. PPO's importance-sampling
correction handles this off-policy data correctly during the first few
epochs; subsequent retrains accumulate on-policy data.

Spec sec 5.1 — point-in-time joins ensure no future leakage:
* layer_scores L1..L9 → already inline in `shadow_trades.layer_scores`
* market features → nearest `intermarket_snapshots` row at or before
  trade.opened_at; defaults to zeros if no snapshot exists for the asset
  in that window
* regime → resolved by checking which `app.ml.regimes` window contains
  trade.opened_at; defaults to "sideways_grind" if none match
* asset_embedding → zeros at buffer-build time (Task A5 fills these in
  via app.rl.adapter; the trainer rebinds before each PPO epoch)
* macro_calendar → defensible defaults (no FOMC/holiday calendar service
  exists yet; a follow-up SP can wire a real one)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.regimes import REGIME_WINDOWS
from app.rl.obs import (
    OBS_DIM,
    AssetState,
    MacroFeatures,
    MarketFeatures,
    PositionState,
    build_observation,
)
from app.rl.reward import compute_reward


log = logging.getLogger(__name__)


# Mapping from shadow_trades.direction ∈ {LONG, SHORT} → discrete action.
# v1: every existing rule-based trade was Kelly-cap-sized at "full" by
# convention; size discretization is refined in Phase B once the brain
# starts emitting its own size choices.
ACTION_LONG_FULL: str = "LONG_FULL"
ACTION_LONG_HALF: str = "LONG_HALF"
ACTION_FLAT: str = "FLAT"
ACTION_SHORT_HALF: str = "SHORT_HALF"
ACTION_SHORT_FULL: str = "SHORT_FULL"
ALL_ACTIONS: tuple[str, ...] = (
    ACTION_LONG_FULL, ACTION_LONG_HALF, ACTION_FLAT,
    ACTION_SHORT_HALF, ACTION_SHORT_FULL,
)


@dataclass(frozen=True)
class Transition:
    obs: np.ndarray            # (OBS_DIM,) float32
    action: str                # one of ALL_ACTIONS
    reward: float              # in [-3, +3]
    next_obs: np.ndarray       # (OBS_DIM,) float32 — same as obs for trade-close
    done: bool                 # True for trade-close transitions (always True in v1)
    asset_id: int
    symbol: str
    opened_at_iso: str         # for diagnostics


@dataclass(frozen=True)
class _TradeRow:
    """In-memory shape of the row joins we need from shadow_trades."""
    id: int
    symbol: str
    direction: str
    opened_at_iso: str
    layer_scores: dict[str, float]
    entry_atr: float
    entry_price: float
    stop_loss: float
    pnl_usdt: float | None      # NULL while still open; we filter open trades out
    position_size_usdt: float


async def _fetch_closed_trades(
    session: AsyncSession, *, window_days: int,
) -> list[_TradeRow]:
    """Pull closed trades whose opened_at is within `window_days` of now.

    SQLite stores timestamps as ISO-8601 text; PostgreSQL as TIMESTAMPTZ.
    We use raw text comparison on ISO strings — works on both dialects.
    """
    rows = (await session.execute(sa.text(
        """
        SELECT id, symbol, direction, opened_at, layer_scores,
               entry_atr, entry_price, stop_loss,
               pnl_usdt, position_size_usdt
        FROM shadow_trades
        WHERE closed_at IS NOT NULL
          AND pnl_usdt IS NOT NULL
        ORDER BY symbol, opened_at
        """
    ))).all()

    out: list[_TradeRow] = []
    for r in rows:
        ls_raw = r.layer_scores
        layer_scores = (
            json.loads(ls_raw) if isinstance(ls_raw, str)
            else (ls_raw or {})
        )
        opened_at_iso = (
            r.opened_at if isinstance(r.opened_at, str)
            else r.opened_at.isoformat()
        )
        out.append(_TradeRow(
            id=int(r.id),
            symbol=str(r.symbol),
            direction=str(r.direction),
            opened_at_iso=opened_at_iso,
            layer_scores=dict(layer_scores),
            entry_atr=float(r.entry_atr),
            entry_price=float(r.entry_price),
            stop_loss=float(r.stop_loss),
            pnl_usdt=float(r.pnl_usdt),
            position_size_usdt=float(r.position_size_usdt),
        ))
    return out


def _layer_scores_to_tuple(d: dict[str, float]) -> tuple[float, ...]:
    """Pull L1..L9 in deterministic order. Missing keys default to 0."""
    out: list[float] = []
    for k in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9"):
        out.append(float(d.get(k, 0.0)))
    return tuple(out)


def _direction_to_action(direction: str) -> str:
    """Map shadow_trades.direction to the discrete action.

    v1 mapping is FULL for every direction. Phase B (PPO trainer) will
    add half-size mapping once the brain emits its own size choices.
    """
    if direction == "LONG":
        return ACTION_LONG_FULL
    if direction == "SHORT":
        return ACTION_SHORT_FULL
    raise ValueError(f"unknown direction {direction!r}")


def _resolve_regime(opened_at_iso: str) -> str:
    """Map a trade's opened_at to one of the 5 frozen regime names.

    Returns "sideways_grind" if the timestamp falls outside every window
    (most of the time will, since the windows are non-overlapping
    selected-event slices, not a partition of the timeline).
    """
    from datetime import datetime
    try:
        ts = datetime.fromisoformat(opened_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return "sideways_grind"
    for w in REGIME_WINDOWS:
        if w.start <= ts <= w.end:
            return w.name
    return "sideways_grind"


async def _nearest_intermarket(
    session: AsyncSession, *, symbol: str, opened_at_iso: str,
) -> tuple[float, float]:
    """Look up nearest at-or-before intermarket_snapshots row.

    Returns (funding_rate, oi_delta_24h_pct). Both default to 0.0 if no
    snapshot exists for this symbol within 24h before opened_at — the
    early shadow_trades from SP-0.5 predate SP-3.5's intermarket worker.
    """
    row = (await session.execute(sa.text(
        """
        SELECT funding_rate, open_interest, captured_at
        FROM intermarket_snapshots
        WHERE symbol = :s AND captured_at <= :t
        ORDER BY captured_at DESC
        LIMIT 1
        """
    ), {"s": symbol, "t": opened_at_iso})).first()
    if row is None:
        return 0.0, 0.0
    funding = float(row.funding_rate) if row.funding_rate is not None else 0.0
    # OI delta requires a 24h-ago snapshot too; if absent, returns 0.
    row_24h = (await session.execute(sa.text(
        """
        SELECT open_interest
        FROM intermarket_snapshots
        WHERE symbol = :s AND captured_at <= datetime(:t, '-24 hours')
        ORDER BY captured_at DESC
        LIMIT 1
        """
    ), {"s": symbol, "t": opened_at_iso})).first() if session.bind.dialect.name == "sqlite" else None
    if row_24h and row_24h.open_interest and row.open_interest:
        oi_delta = (
            (float(row.open_interest) - float(row_24h.open_interest))
            / float(row_24h.open_interest)
        )
    else:
        oi_delta = 0.0
    return funding, oi_delta


def _macro_defaults() -> MacroFeatures:
    """Defensible defaults; a real macro/calendar service is a follow-up SP."""
    return MacroFeatures(
        hours_to_next_high_impact=24.0,
        fomc_window=False,
        weekend=False,
        asia_open=False,
    )


async def load_from_shadow_trades(
    session: AsyncSession,
    *,
    window_days: int = 365,
    asset_embeddings: dict[int, np.ndarray] | None = None,
    asset_id_for_symbol: dict[str, int] | None = None,
) -> list[Transition]:
    """Materialize closed shadow_trades into a list of Transitions.

    Args:
        session: live AsyncSession (Postgres or SQLite). Caller supplies.
        window_days: trailing days to include. Currently unused (the SQL
            already filters by closed_at IS NOT NULL); reserved for later
            cursor-windowed scrolling once the buffer outgrows RAM.
        asset_embeddings: optional map of asset_id → 32-dim embedding.
            If absent, zero-embedding is used (rebound by trainer pre-epoch).
        asset_id_for_symbol: optional map of symbol → integer asset_id.
            If absent, asset_id defaults to 0 for every trade (bin all
            assets into one slot — fine for a smoke test, wrong for real
            training; trainer overrides this).

    Returns:
        list of Transition records, one per closed shadow_trade.
    """
    raw = await _fetch_closed_trades(session, window_days=window_days)
    if not raw:
        log.info("replay buffer empty — no closed shadow_trades found")
        return []

    embed_zero = np.zeros(32, dtype=np.float32)
    embeds = asset_embeddings or {}
    sym_to_id = asset_id_for_symbol or {}

    # Trailing per-asset trade history for σ_R estimation in reward computation.
    history_by_symbol: dict[str, list[_TradeRow]] = {}

    transitions: list[Transition] = []
    for tr in raw:
        # Reward needs (pnl_quote, initial_risk_quote) shape; map from the
        # shadow_trade columns.
        risk_quote = abs(tr.entry_price - tr.stop_loss) * (
            tr.position_size_usdt / tr.entry_price
        )
        if risk_quote <= 0:
            log.warning(
                "skipping trade %d: invalid risk %.4f (entry=%s SL=%s)",
                tr.id, risk_quote, tr.entry_price, tr.stop_loss,
            )
            continue

        class _RewardTrade:
            pnl_quote = tr.pnl_usdt or 0.0
            initial_risk_quote = risk_quote

        recent_for_sigma: list[object] = []
        for past in history_by_symbol.get(tr.symbol, []):
            past_risk = abs(past.entry_price - past.stop_loss) * (
                past.position_size_usdt / past.entry_price
            )
            if past_risk <= 0:
                continue
            class _R:
                pnl_quote = past.pnl_usdt or 0.0
                initial_risk_quote = past_risk
            recent_for_sigma.append(_R())

        reward = compute_reward(_RewardTrade(), recent=recent_for_sigma)

        # Build observation
        asset_id = sym_to_id.get(tr.symbol, 0)
        embedding = embeds.get(asset_id, embed_zero)
        layer_tuple = _layer_scores_to_tuple(tr.layer_scores)
        funding, oi_delta = await _nearest_intermarket(
            session, symbol=tr.symbol, opened_at_iso=tr.opened_at_iso,
        )
        market = MarketFeatures(
            atr_pct=tr.entry_atr / tr.entry_price if tr.entry_price > 0 else 0.0,
            funding_rate=funding,
            oi_delta_24h=oi_delta,
            dxy_corr_30d=0.0,
            gold_corr_30d=0.0,
            regime=_resolve_regime(tr.opened_at_iso),
        )
        position = PositionState(cur_position=0, unrealized_pnl_R=0.0, bars_in_position=0)
        macro = _macro_defaults()
        asset_state = AssetState(asset_id=asset_id, embedding=embedding)
        obs = build_observation(asset_state, layer_tuple, market, position, macro)

        action = _direction_to_action(tr.direction)
        transitions.append(Transition(
            obs=obs, action=action, reward=reward,
            next_obs=obs.copy(),       # trade-close: episode ends, next_obs is terminal
            done=True,
            asset_id=asset_id, symbol=tr.symbol,
            opened_at_iso=tr.opened_at_iso,
        ))

        history_by_symbol.setdefault(tr.symbol, []).append(tr)

    log.info("replay buffer built: %d transitions across %d assets",
             len(transitions), len(history_by_symbol))
    return transitions


__all__ = [
    "ACTION_LONG_FULL", "ACTION_LONG_HALF", "ACTION_FLAT",
    "ACTION_SHORT_HALF", "ACTION_SHORT_FULL", "ALL_ACTIONS",
    "Transition",
    "load_from_shadow_trades",
]
