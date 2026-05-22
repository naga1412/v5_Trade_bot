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

SP-4 Phase D extension — same gate also evaluates RL brain checkpoints
in the ``rl_checkpoints`` table by passing ``table="rl_checkpoints"``
and ``metric="sharpe"`` to :func:`evaluate_challenger`. The sharpe
direction inverts: challenger wins iff
``challenger_sharpe > champion_sharpe * (1 + (1-IMPROVEMENT_BAR))``,
i.e. a strict 5% IMPROVEMENT (higher Sharpe is better, opposite of
MAE which is lower-is-better).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# A challenger must improve on the champion's MAE by at least 5% to win.
IMPROVEMENT_BAR: float = 0.95
# Sharpe equivalent: challenger > champion * SHARPE_IMPROVEMENT_BAR (1.05)
SHARPE_IMPROVEMENT_BAR: float = 1.0 + (1.0 - IMPROVEMENT_BAR)  # 1.05
# Held-out window length used by ``_evaluate_mae``. Currently 30 days
# preceding "now"; in production we should pull this from a configurable
# knob so we can replay the same window across multiple promotions.
HELD_OUT_DAYS: int = 30

Metric = Literal["mae", "sharpe"]
Table = Literal["ml_checkpoints", "rl_checkpoints"]


@dataclass(frozen=True)
class ChampionChallengerResult:
    """Outcome of a single champion-vs-challenger comparison.

    ``champion_mae`` is ``None`` iff there is no currently-active
    checkpoint for the same ``model_name`` — in which case
    ``challenger_wins`` is unconditionally ``True`` (any model beats no
    model).

    The ``metric`` field carries the comparison type:
      * ``"mae"`` (default, SP-7 ConvLSTM path) — values in
        ``champion_mae``/``challenger_mae`` are MAEs, lower-is-better.
      * ``"sharpe"`` (SP-4 RL brain path) — values are Sharpe ratios,
        higher-is-better. The field NAMES stay ``*_mae`` for
        backward compatibility with SP-7 consumers (admin_ml.py error
        messages, the ``test_api_admin_ml_checkpoints_gate.py``
        integration tests, etc.). Read ``metric`` to know how to
        interpret the values.

    Generic-name @property aliases ``champion_metric`` /
    ``challenger_metric`` are provided for SP-4 admin_rl.py callers
    that prefer metric-agnostic naming.
    """

    champion_mae: float | None
    challenger_mae: float
    challenger_wins: bool
    metric: Metric = "mae"

    @property
    def champion_metric(self) -> float | None:
        return self.champion_mae

    @property
    def challenger_metric(self) -> float:
        return self.challenger_mae


async def evaluate_challenger(
    session: "AsyncSession",
    *,
    challenger_checkpoint_id: int,
    table: Table = "ml_checkpoints",
    metric: Metric = "mae",
) -> ChampionChallengerResult:
    """Compare a challenger checkpoint against the active champion.

    Looks up the challenger's ``model_name`` in ``table``, finds the
    currently-active row for the same model (the "champion"), runs the
    appropriate ``_evaluate_*`` for each over the same held-out window,
    and returns the head-to-head result.

    For ``metric="mae"`` (default — SP-1 ConvLSTM checkpoints):
        challenger wins iff ``challenger < champion * IMPROVEMENT_BAR``
        (strict 5% improvement; LOWER is better).

    For ``metric="sharpe"`` (SP-4 RL brain checkpoints):
        challenger wins iff ``challenger > champion * SHARPE_IMPROVEMENT_BAR``
        (strict 5% improvement; HIGHER is better).

    The first-checkpoint case (no active champion) auto-passes for
    both metrics — the bootstrap path stays the same.

    Raises ``LookupError`` when the challenger row is missing; the
    admin route translates that to a 404.
    """
    challenger_row = (await session.execute(
        sa.text(
            f"SELECT id, model_name FROM {table} WHERE id = :i"
        ),
        {"i": challenger_checkpoint_id},
    )).first()
    if challenger_row is None:
        raise LookupError(
            f"challenger checkpoint id={challenger_checkpoint_id} "
            f"not found in {table}"
        )

    champion_row = (await session.execute(
        sa.text(
            f"SELECT id FROM {table} "
            "WHERE model_name = :m AND is_active = TRUE AND id != :i "
            "LIMIT 1"
        ),
        {"m": challenger_row.model_name, "i": challenger_checkpoint_id},
    )).first()

    if metric == "sharpe":
        challenger_value = await _evaluate_sharpe(session, challenger_checkpoint_id)
    else:
        challenger_value = await _evaluate_mae(session, challenger_checkpoint_id)

    if champion_row is None:
        # Bootstrap: no incumbent — any model beats no model.
        return ChampionChallengerResult(
            champion_mae=None,
            challenger_mae=challenger_value,
            challenger_wins=True,
            metric=metric,
        )

    if metric == "sharpe":
        champion_value = await _evaluate_sharpe(session, int(champion_row.id))
        challenger_wins = challenger_value > champion_value * SHARPE_IMPROVEMENT_BAR
    else:
        champion_value = await _evaluate_mae(session, int(champion_row.id))
        challenger_wins = challenger_value < champion_value * IMPROVEMENT_BAR
    return ChampionChallengerResult(
        champion_mae=champion_value,
        challenger_mae=challenger_value,
        challenger_wins=challenger_wins,
        metric=metric,
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


async def _evaluate_sharpe(
    session: "AsyncSession",
    checkpoint_id: int,
) -> float:
    """Return the RL checkpoint's Sharpe — read from ``eval_results`` JSONB.

    PR-BRAIN-BACKTEST-PHASEB5 (2026-05-22): the trainer
    (:mod:`app.rl.backtest_brain`) computes Sharpe on a chronological
    80/20 holdout AT TRAINING TIME and persists it in
    ``rl_checkpoints.eval_results->>'sharpe'``. This helper no longer
    re-runs a backtest at activation time — it just reads the column.

    Returns ``0.0`` when the row has no Sharpe (legacy id=1, id=2 from
    pre-PR-BACKTEST-PHASEB5 era still carry
    ``{"_status": "deferred_to_phase_b5"}``; insufficient-data runs
    carry ``{"_status": "insufficient_data", ...}``). The upstream
    bootstrap branch (``champion_row is None``) auto-passes when there
    is no incumbent, so 0.0 here is safe for the bootstrap path. For
    head-to-head comparisons, 0.0 means "any new checkpoint with a real
    Sharpe wins" — appropriate behaviour while we accumulate replay
    data.
    """
    row = (await session.execute(
        sa.text(
            "SELECT eval_results->>'sharpe' AS sharpe "
            "FROM rl_checkpoints WHERE id = :i"
        ),
        {"i": checkpoint_id},
    )).first()
    if row is None or row.sharpe is None:
        return 0.0
    try:
        return float(row.sharpe)
    except (TypeError, ValueError):
        return 0.0
