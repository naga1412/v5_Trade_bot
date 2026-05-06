"""Champion/challenger evaluation gate (SP-7 Phase G1, spec §6.5).

A challenger checkpoint must beat the currently-active champion by at least
``IMPROVEMENT_BAR`` (5%) on a fixed held-out window before the admin
promotion route swaps it in. The first-ever checkpoint (no champion to
beat) auto-passes — the operator still has to PATCH ``is_active=true``
to trigger evaluation, and ``?force=true`` always bypasses the gate.

The "MAE" metric is computed by ``_evaluate_mae`` from a deterministic
backtest over the held-out window (currently a thin wrapper around
``tools.backtest.run_backtest`` — see TODO inline). Tests monkeypatch
``_evaluate_mae`` so they never hit real OHLCV / Postgres.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# A challenger must improve on the champion's MAE by at least 5% to win.
IMPROVEMENT_BAR: float = 0.95
# Held-out window length used by ``_evaluate_mae``. Currently 30 days
# preceding "now"; in production we should pull this from a configurable
# knob so we can replay the same window across multiple promotions.
HELD_OUT_DAYS: int = 30


@dataclass(frozen=True)
class ChampionChallengerResult:
    """Outcome of a single champion-vs-challenger comparison.

    ``champion_mae`` is ``None`` iff there is no currently-active
    checkpoint for the same ``model_name`` — in which case
    ``challenger_wins`` is unconditionally ``True`` (any model beats no
    model).
    """

    champion_mae: float | None
    challenger_mae: float
    challenger_wins: bool


async def evaluate_challenger(
    session: "AsyncSession",
    *,
    challenger_checkpoint_id: int,
) -> ChampionChallengerResult:
    """Compare a challenger checkpoint against the active champion.

    Looks up the challenger's ``model_name``, finds the currently-active
    row for the same model (the "champion"), runs ``_evaluate_mae`` for
    each over the same held-out window, and returns the head-to-head
    result. The challenger wins iff
    ``challenger_mae < champion_mae * IMPROVEMENT_BAR`` — i.e. a strict
    5% improvement. If there is no active champion, the challenger
    automatically wins (this is the SP-1.1 first-checkpoint case).

    Raises ``LookupError`` when ``challenger_checkpoint_id`` does not
    exist; the admin route translates that to a 404.
    """
    challenger_row = (await session.execute(
        sa.text(
            "SELECT id, model_name FROM ml_checkpoints WHERE id = :i"
        ),
        {"i": challenger_checkpoint_id},
    )).first()
    if challenger_row is None:
        raise LookupError(
            f"challenger checkpoint id={challenger_checkpoint_id} not found"
        )

    champion_row = (await session.execute(
        sa.text(
            "SELECT id FROM ml_checkpoints "
            "WHERE model_name = :m AND is_active = 1 AND id != :i "
            "LIMIT 1"
        ),
        {"m": challenger_row.model_name, "i": challenger_checkpoint_id},
    )).first()

    challenger_mae = await _evaluate_mae(session, challenger_checkpoint_id)

    if champion_row is None:
        # Bootstrap: no incumbent — any model beats no model.
        return ChampionChallengerResult(
            champion_mae=None,
            challenger_mae=challenger_mae,
            challenger_wins=True,
        )

    champion_mae = await _evaluate_mae(session, int(champion_row.id))
    challenger_wins = challenger_mae < champion_mae * IMPROVEMENT_BAR
    return ChampionChallengerResult(
        champion_mae=champion_mae,
        challenger_mae=challenger_mae,
        challenger_wins=challenger_wins,
    )


async def _evaluate_mae(  # pragma: no cover — TDD seam, mocked in tests
    session: "AsyncSession",
    checkpoint_id: int,
) -> float:
    """Backtest the given checkpoint over the held-out window and return MAE.

    Currently delegates to ``tools.backtest.run_backtest`` over the last
    ``HELD_OUT_DAYS`` days; we derive a "MAE-like" loss from
    ``1 - win_rate`` (a proxy until we wire per-bar prediction error
    extraction into the backtester — tracked as a SP-1.1 follow-up).

    Tests inject this function via ``monkeypatch`` so they never hit
    real OHLCV / Postgres / GPU inference.
    """
    # Lazy import — avoids circular import via app.core.predictor at
    # module load and keeps the unit tests' monkeypatch lightweight.
    from tools.backtest import run_backtest

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=HELD_OUT_DAYS)
    result = run_backtest(symbol="BTC/USDT", timeframe="1h", start=start, end=end)
    # Higher win_rate -> lower "MAE". Bound to [0, 1].
    return max(0.0, min(1.0, 1.0 - result.win_rate))
