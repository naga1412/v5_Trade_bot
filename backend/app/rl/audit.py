"""SP-4 Phase C3 — append-only hash-chained writes to ``brain_decisions``.

Every brain inference call in production produces one row in the
``brain_decisions`` table (migration 0015) carrying the observation,
the raw + smoothed action, the policy logits, and the value estimate.
The row is hash-chained (``prev_hash`` / ``row_hash``) using the same
canonical-JSON-SHA256 scheme as ``predictions`` and ``shadow_trades``,
so SP-7's ``verify_chain`` worker picks it up alongside those tables
without any new code.

Public surface:

* :func:`record_brain_decision` — given a populated
  :class:`~app.rl.inference.BrainDecision`, write the row and return
  the new ``row_hash``. Caller commits the session.

The observation vector is serialized as a list of floats (not the
named-key dict the spec sec 3.2 alluded to) — that's a smaller payload
and the ordering is documented in :mod:`app.rl.obs`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_with_chain
from app.rl.inference import BrainDecision


log = logging.getLogger(__name__)


def _to_jsonable(obs: Any) -> str:
    """Serialise an observation vector for JSONB storage.

    Accepts numpy arrays or plain sequences. Returns a JSON string
    (not a dict) so the migration's TEXT/JSONB column accepts it on
    both PostgreSQL and SQLite backends.
    """
    if hasattr(obs, "tolist"):
        return json.dumps(obs.tolist(), separators=(",", ":"))
    return json.dumps(list(obs), separators=(",", ":"))


async def record_brain_decision(
    session: AsyncSession,
    *,
    decision: BrainDecision,
    symbol: str,
    observation: Any,
    ts: datetime | None = None,
) -> str:
    """Insert one ``brain_decisions`` row with hash-chain integrity.

    Returns the new ``row_hash``. Caller is responsible for
    ``session.commit()``.

    Args:
        session: live AsyncSession.
        decision: the BrainDecision returned by ``decide_action``.
        symbol: market symbol the decision applies to (e.g. ``"BTC/USDT"``).
        observation: compact observation-input serialisation (see
            ``predictor_glue._serialise_inputs``) — numpy array, list, or
            tuple. Serialised via :func:`_to_jsonable`.
        ts: timestamp the candle closed at; defaults to now-UTC if absent.
    """
    payload = {
        # Datetime passes through raw — Postgres TIMESTAMPTZ rejects ISO
        # strings via asyncpg; SQLite tests still bind datetime->TEXT.
        "ts": ts or datetime.now(timezone.utc),
        "symbol": symbol,
        "checkpoint_id": decision.checkpoint_id,
        "observation": _to_jsonable(observation),
        "action": decision.raw_action,
        "action_logits": json.dumps(decision.logits, separators=(",", ":")),
        "value_estimate": float(decision.value_estimate),
        "smoothed_action": decision.smoothed_action,
    }
    return await insert_with_chain(session, "brain_decisions", payload)


__all__ = ["record_brain_decision"]
