"""Champion/challenger evaluation gate (SP-7 Phase G1, spec §6.5).

A challenger checkpoint must beat the currently-active champion by at least
``IMPROVEMENT_BAR`` (5%) on a fixed held-out window before the admin
promotion route swaps it in.

THE NO-CHAMPION CASE REFUSES BY DEFAULT (2026-10-26). It used to
auto-pass, which made "there is no incumbent" the most permissive state
this gate had: whatever was presented got promoted with no quality check
of any kind. #571 closed the identical branch in ``register_brain.py``
after it silently installed untested brains for 64 nights and was then
tripped by its own verification probe; this is the same family, reached
through the admin routes and the ConvLSTM promotion path instead of the
nightly cron. Pass ``allow_bootstrap=True`` to opt in deliberately.

The outcome is NAMED rather than inferred from ``challenger_wins``:
  * ``compared``          — a champion existed and the bar was applied.
  * ``bootstrap_blocked`` — no champion, no opt-in. ``challenger_wins``
    is False. This is the default.
  * ``bootstrap``         — no champion AND ``allow_bootstrap=True``.

``?force=true`` on either admin route still bypasses the gate entirely
(logged at WARNING), so no operator capability is removed — the bypass
is just no longer the silent default.

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

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# A challenger must improve on the champion's MAE by at least 5% to win.
IMPROVEMENT_BAR: float = 0.95
# Multiplicative Sharpe improvement bar: challenger > champion * 1.05.
SHARPE_IMPROVEMENT_BAR: float = 1.0 + (1.0 - IMPROVEMENT_BAR)  # 1.05
# Additive Sharpe floor: applied when the multiplicative bar would invert for
# non-positive champion Sharpe (e.g. champion=-2.0 → bar=-2.1, which lets
# a worse challenger pass). challenger must also exceed champion + this delta.
SHARPE_MIN_DELTA: float = 0.05
# Held-out window length used by ``_evaluate_mae``. Currently 30 days
# preceding "now"; in production we should pull this from a configurable
# knob so we can replay the same window across multiple promotions.
HELD_OUT_DAYS: int = 30

Metric = Literal["mae", "sharpe"]
Table = Literal["ml_checkpoints", "rl_checkpoints"]
# Why a named outcome and not just ``challenger_wins``: a False from the
# bootstrap branch and a False from a genuinely-losing challenger demand
# different operator responses, and the 422 that reports "needs 5%
# improvement" is actively misleading when there was no champion to
# improve on. The name is what the routes render.
GateOutcome = Literal["compared", "bootstrap", "bootstrap_blocked"]


def sharpe_passes(challenger: float, champion: float) -> bool:
    """Return True iff challenger clears the Sharpe improvement bar.

    Uses ``max(champion * SHARPE_IMPROVEMENT_BAR, champion + SHARPE_MIN_DELTA)``
    so the bar is always an additive improvement even when champion <= 0.
    Without the additive floor a negative champion (-2.0) sets the
    multiplicative threshold at -2.1, which lets a *worse* challenger
    (-2.05) pass the gate.

    Called by both the HTTP PATCH route (via ``evaluate_challenger``) and
    ``tools/ml/register_brain.py``'s ``--direct`` DB path so the two code
    paths stay in sync.
    """
    threshold = max(champion * SHARPE_IMPROVEMENT_BAR, champion + SHARPE_MIN_DELTA)
    return challenger > threshold


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
    gate_outcome: GateOutcome = "compared"

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
    allow_bootstrap: bool = False,
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

    The first-checkpoint case (no active champion) REFUSES unless
    ``allow_bootstrap=True`` is passed explicitly, for both metrics.
    See the module docstring for why the previous auto-pass was the
    defect and not the feature.

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
        # No incumbent. "Any model beats no model" was the old reading and
        # it is wrong: nothing here has established that the challenger is
        # any good, only that it is unopposed. Refuse unless asked.
        return ChampionChallengerResult(
            champion_mae=None,
            challenger_mae=challenger_value,
            challenger_wins=allow_bootstrap,
            metric=metric,
            gate_outcome="bootstrap" if allow_bootstrap else "bootstrap_blocked",
        )

    if metric == "sharpe":
        champion_value = await _evaluate_sharpe(session, int(champion_row.id))
        challenger_wins = sharpe_passes(challenger_value, champion_value)
    else:
        champion_value = await _evaluate_mae(session, int(champion_row.id))
        challenger_wins = challenger_value < champion_value * IMPROVEMENT_BAR
    return ChampionChallengerResult(
        champion_mae=champion_value,
        challenger_mae=challenger_value,
        challenger_wins=challenger_wins,
        metric=metric,
        gate_outcome="compared",
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


class MalformedSharpeError(ValueError):
    """A checkpoint's stored Sharpe is present but cannot be trusted.

    Operator ruling 2026-08-08 (defect-sweep item #19): distinct from
    "no Sharpe recorded" (row missing, NULL, or a documented ``_status``
    sentinel), which is an intentional 0.0 fallback — see
    ``_evaluate_sharpe``'s docstring. This exception is for the case
    where a Sharpe VALUE IS present but is unusable (unparseable, NaN,
    or infinite). Silently coercing that to 0.0 would let a
    corrupted/broken checkpoint beat a realistically-negative champion
    (e.g. -7.02) and get promoted — a promotion-gate bypass, not a
    logging nit. Never caught internally; must propagate to the caller.
    """

    def __init__(self, checkpoint_id: int, raw_value: object) -> None:
        self.checkpoint_id = checkpoint_id
        self.raw_value = raw_value
        super().__init__(
            f"checkpoint id={checkpoint_id} has a malformed sharpe value "
            f"{raw_value!r} — refusing to coerce to 0.0"
        )


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

    Raises :class:`MalformedSharpeError` (operator ruling 2026-08-08)
    when the ``sharpe`` key IS present but is unparseable, NaN, or
    infinite — those are refused outright rather than coerced to 0.0,
    which would otherwise silently bypass the champion-challenger gate.
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
        value = float(row.sharpe)
    except (TypeError, ValueError) as e:
        raise MalformedSharpeError(checkpoint_id, row.sharpe) from e
    if math.isnan(value) or math.isinf(value):
        raise MalformedSharpeError(checkpoint_id, row.sharpe)
    return value
