"""Pure builder functions for DB row payloads.

PR1 Phase 2.2 — extract the dict-literal construction from the 4 call sites
that persist to `predictions`, `shadow_trades`, and `live_trades` into
testable, type-annotated pure functions.

Rules:
  * No I/O, no DB lookups, no external calls.
  * All side-effect-producing code stays at the call sites.
  * Bit-identical output with respect to the original inline dicts.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from app.shadow.engine import Direction


def build_predictions_payload(
    pred: Any,  # LivePredictionOut-shaped (duck-typed for testability)
    *,
    user_id: int,
    layer_payload: dict[str, Any],
    ghost_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the dict passed to ``persist_prediction`` for the predictions table.

    ``layer_payload`` is the already-constructed dict that will be
    serialized into the ``layer_scores`` JSONB column. The caller owns
    its construction (dict-comprehension over ``pred.layer_scores`` +
    update with ``pred.prediction_extras`` if not None) — this keeps the
    same Python object available to downstream consumers without a
    JSON round-trip.

    Then builds the 11-key base dict (serializing ``layer_payload`` to
    JSON for the column), and ``update``s with ``ghost_payload`` if
    truthy.

    Bit-identical reference: backend/app/ws/live_prediction.py:122-144.
    """
    result: dict[str, Any] = {
        "user_id": user_id,
        "symbol": pred.symbol,
        "timeframe": pred.timeframe,
        "ts": pred.ts,
        "layer_scores": json.dumps(layer_payload),
        "final_score": pred.final.score,
        "direction": pred.final.direction,
        "confidence": pred.final.confidence,
        "inputs_hash": pred.inputs_hash,
        "model_version": "sp-0",
        "cold_start": pred.cold_start,
    }

    if ghost_payload:
        result.update(ghost_payload)

    return result


def build_shadow_trade_payload(
    pos: Any,  # ShadowPosition-shaped (duck-typed)
    *,
    user_id: int,
    exit_price: float,
    exit_reason: Any,  # ExitReason enum — accessed as .value
    closed_at: datetime,
    bars_held: int,
    inputs_hash: str,
) -> dict[str, Any]:
    """Build the dict passed to ``insert_with_chain`` for the shadow_trades table.

    Computes ``pnl_pct`` and ``pnl_usdt`` internally (sign flips per direction).
    Direction comparison uses ``is Direction.LONG`` to match the original exactly.

    Bit-identical reference: backend/app/shadow/persistence.py:118-148.
    """
    if pos.direction is Direction.LONG:
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
    else:
        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100.0
    pnl_usdt = pos.position_size_usdt * pnl_pct / 100.0

    return {
        "user_id": user_id,
        "symbol": pos.symbol,
        "timeframe": "1h",
        "direction": pos.direction.value,
        "entry_price": pos.entry_price,
        "stop_loss": pos.stop_loss,
        "take_profit": pos.take_profit,
        "position_size_usdt": pos.position_size_usdt,
        "entry_score": pos.entry_score,
        "entry_confidence": pos.entry_confidence,
        "layer_scores": json.dumps(pos.layer_scores),
        "entry_atr": pos.entry_atr,
        "exit_price": exit_price,
        "exit_reason": exit_reason.value,
        "pnl_pct": pnl_pct,
        "pnl_usdt": pnl_usdt,
        "bars_held": bars_held,
        "opened_at": pos.opened_at,
        "closed_at": closed_at,
        "inputs_hash": inputs_hash,
        "model_version": "sp-0.5",
        "signal_id": pos.signal_id,
    }


def build_live_trade_payload(
    *,
    user_id: int,
    symbol: str,
    direction: str,
    margin_usdt: float,
    leverage: int,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    binance_order_id: str,
    opened_at: datetime,
    mode_at_open: str,
    approved_via: Literal["auto", "telegram"],
    reasoning_json: str,
    inputs_hash: str,
) -> dict[str, Any]:
    """Build the dict passed to ``insert_with_chain`` for the live_trades table.

    Computes ``position_value_usdt = margin_usdt * leverage`` internally.
    ``reasoning_json`` is already a JSON-serialized string from the caller
    (each call site serializes its own dict via ``json.dumps`` before calling
    this builder — do NOT call ``json.dumps`` again here).

    Key order matches the original inline dicts exactly:
    user_id, symbol, direction, margin_usdt, leverage, position_value_usdt,
    entry_price, stop_loss, take_profit, binance_order_id, opened_at,
    mode_at_open, approved_via, reasoning, inputs_hash.

    Bit-identical reference:
      backend/app/trading/execution/dispatcher.py:353-373 (auto path)
      backend/app/ops/telegram_polling.py:192-211 (telegram path)
    """
    return {
        "user_id": user_id,
        "symbol": symbol,
        "direction": direction,
        "margin_usdt": margin_usdt,
        "leverage": leverage,
        "position_value_usdt": margin_usdt * leverage,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "binance_order_id": binance_order_id,
        "opened_at": opened_at,
        "mode_at_open": mode_at_open,
        "approved_via": approved_via,
        "reasoning": reasoning_json,
        "inputs_hash": inputs_hash,
    }
