"""Prediction accuracy validator (Feature 2 — honest telemetry).

Lifecycle:
  1. ``record_pending_validation`` is called inside the live-prediction
     persist path with the anchor candle's close price. Inserts a row
     with ``target_close=NULL`` and ``target_ts = anchor_ts + 1 timeframe``.
  2. The 60-second background loop ``run_validator_loop`` queries pending
     rows whose ``target_ts < now()``, fetches the actual close from
     Binance klines, computes ``was_correct`` + ``pnl_pct``, and updates
     the row.
  3. The API endpoint in ``app.api.routes.predictions`` aggregates over
     these rows to surface rolling accuracy by symbol/timeframe.

The validator is read-then-write only — it never crashes the worker on
a bad row (missing kline, parse error, etc.), it logs and skips so a
single bad row doesn't block the others. NEUTRAL predictions are
considered correct when the actual move is below a configurable epsilon
(default 0.1% of anchor price).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.bar_fetch import UnresolvedMarket, fetch_bars_by_symbol
from app.ops.check_events import record_queue_pass
from app.ops.heartbeat import record_heartbeat

log = logging.getLogger(__name__)

VALIDATOR_INTERVAL_SECONDS: int = 60
NEUTRAL_EPSILON_PCT: float = 0.1  # below this absolute % move, NEUTRAL is "correct"
WORKER_NAME: str = "prediction_validator_task"
# How many non-validating attempts a row gets before it is dead-lettered.
# 6 attempts at VALIDATOR_INTERVAL_SECONDS=60 is ~6 minutes, which is
# generous for a transient Binance/network blip and fast enough that a
# genuinely un-validatable row stops blocking the queue within one
# scoring cycle. Deliberately a module constant and NOT an env var: an
# env flip is operator-only, and this value needs no tuning knob -- the
# failure it bounds is permanent-vs-transient, not a threshold to fit.
MAX_VALIDATION_ATTEMPTS: int = 6


_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


def _timeframe_seconds(tf: str) -> int:
    return _TIMEFRAME_SECONDS.get(tf, 3600)


async def record_pending_validation(
    session: AsyncSession,
    *,
    prediction_id: int | None,
    user_id: int,
    symbol: str,
    timeframe: str,
    direction: str,
    score: float,
    confidence: float,
    anchor_ts: datetime,
    anchor_close: float,
) -> None:
    """Insert one pending row when a prediction is created.

    ``prediction_id`` is best-effort — callers that don't have the parent
    row id pass ``None`` and the validator still works (aggregation is by
    symbol+timeframe, not by id). The hash-chained ``predictions`` table
    can be joined separately for audit purposes if needed.
    """
    target_ts = anchor_ts + timedelta(seconds=_timeframe_seconds(timeframe))
    try:
        await session.execute(
            sa.text(
                "INSERT INTO prediction_validations "
                "(prediction_id, user_id, symbol, timeframe, direction, "
                "score, confidence, anchor_ts, anchor_close, target_ts) "
                "VALUES (:pid, :uid, :sym, :tf, :dir, :sc, :cf, :ats, :ac, :tts)"
            ),
            {
                "pid": prediction_id, "uid": user_id,
                "sym": symbol, "tf": timeframe, "dir": direction,
                "sc": score, "cf": confidence,
                "ats": anchor_ts, "ac": anchor_close, "tts": target_ts,
            },
        )
    except Exception as e:  # noqa: BLE001 — telemetry is best-effort
        log.warning(
            "record_pending_validation failed for %s/%s @ %s: %s",
            symbol, timeframe, anchor_ts, e,
        )


def _classify_correct(
    direction: str,
    anchor_close: float,
    target_close: float,
    epsilon_pct: float = NEUTRAL_EPSILON_PCT,
) -> tuple[bool, float]:
    """Return ``(was_correct, pnl_pct)`` for a closed validation row.

    ``pnl_pct`` is signed: positive when the prediction direction
    matched the realized move, negative when opposite, near-zero for
    NEUTRAL classification.
    """
    move_pct = (target_close - anchor_close) / anchor_close * 100.0
    if direction == "LONG":
        return (move_pct > 0, move_pct)
    if direction == "SHORT":
        return (move_pct < 0, -move_pct)
    # NEUTRAL — correct iff the actual move stayed inside the epsilon band.
    return (abs(move_pct) <= epsilon_pct, move_pct)


async def _fetch_close_at(
    http: httpx.AsyncClient, symbol: str, timeframe: str, ts: datetime,
) -> float | None:
    """Fetch the closing price for the bar whose open-time equals ``ts``.

    Propagates :class:`UnresolvedMarket` on purpose -- see below.
    """
    binance_sym = symbol.replace("/", "")
    # Pull a window around the target so we don't lose a few-millisecond drift.
    start_ms = int(ts.timestamp() * 1000)
    try:
        # FU-53: was BinanceClient.fetch_klines -- i.e. SPOT for every
        # symbol, so every futures-only symbol silently validated against
        # a market it is not listed on and returned nothing.
        # prediction_validations carries no symbol_source column, so this
        # asks the Binance listing caches instead.
        #
        # UnresolvedMarket is deliberately NOT caught here. The listing
        # caches answer by positive membership only, so "cannot resolve"
        # means the market is genuinely unknown -- and the one thing we
        # must not do with an unknown market is pick spot, which is #539.
        # Letting it propagate leaves the row pending for the next 60s
        # cycle (the correct outcome: unknown now, probably known once
        # app.workers.futures_only_refresh completes a cycle) and surfaces
        # the reason in validate_pending's per-row log, carrying the
        # exception's full argument. Converting it to a None return here
        # would be a second skip path for a caller to forget.
        bars = await fetch_bars_by_symbol(
            binance_sym, timeframe, http=http, limit=3,
        )
    except (httpx.HTTPStatusError, httpx.HTTPError) as e:
        log.info("validator fetch_klines failed for %s/%s: %s", symbol, timeframe, e)
        return None
    if not bars:
        return None
    # Find the bar whose open-time is closest to our target_ts. We accept
    # the closest one — Binance occasionally returns slightly off-aligned
    # ts on low-volume symbols, but for major pairs the alignment is exact.
    best = min(bars, key=lambda b: abs(int(b.ts.timestamp() * 1000) - start_ms))
    return float(best.close)


async def _validate_one(
    session: AsyncSession, http: httpx.AsyncClient, row: sa.Row,
) -> bool:
    """Validate one pending row. Returns True on update, False on skip."""
    close = await _fetch_close_at(http, row.symbol, row.timeframe, row.target_ts)
    if close is None:
        return False
    was_correct, pnl_pct = _classify_correct(row.direction, float(row.anchor_close), close)
    # Bind validated_at from Python so the same SQL works on Postgres
    # AND on the SQLite test mirror (SQLite has no NOW() function).
    await session.execute(
        sa.text(
            "UPDATE prediction_validations "
            "SET target_close = :tc, was_correct = :wc, pnl_pct = :pp, "
            "validated_at = :now "
            "WHERE id = :id"
        ),
        {
            "tc": close, "wc": was_correct, "pp": pnl_pct,
            "now": datetime.now(timezone.utc), "id": row.id,
        },
    )
    return True


@dataclass(frozen=True)
class ValidationPass:
    """What one validator pass actually did.

    SELECTED IS THE FIELD THAT WAS MISSING. The loop already recorded
    ``validated_in_tick`` on every heartbeat, so ``advanced == 0`` was
    visible for all 53 days of the 2026-09-03 outage -- and meant
    nothing, because zero validations is the correct and expected state
    when nothing is due. Only the PAIR is diagnostic:
    ``selected > 0 and advanced == 0`` is a stuck queue and cannot be
    anything else.
    """

    selected: int
    advanced: int
    skipped: int
    dead_lettered: int
    unresolved: int

    @property
    def stalled(self) -> bool:
        """Work was available and none of it progressed."""
        return self.selected > 0 and self.advanced == 0


async def validate_pending_detailed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    http: httpx.AsyncClient | None = None,
    now: datetime | None = None,
    batch_limit: int = 100,
) -> ValidationPass:
    """Run one validator pass and report what it did.

    SELECTION NOW EXCLUDES DEAD-LETTERED ROWS. A row whose
    ``unvalidatable_reason`` is set has exhausted
    ``MAX_VALIDATION_ATTEMPTS`` and is permanently out of the eligible
    set, so it can no longer sit at the head of an oldest-first queue
    and starve everything behind it. That starvation is the actual
    defect; see migration 0045's docstring for the measured 129-row
    poison against ``batch_limit=100``.

    EVERY NON-VALIDATING ATTEMPT IS COUNTED. The old code's
    ``return False`` was indistinguishable from "nothing to do" at every
    layer above it. Now the attempt lands in the row's own
    ``validation_attempts``, and the pass total lands in the returned
    :class:`ValidationPass`.

    ``validate_pending`` remains the int-returning entry point for every
    existing caller -- a deliberately thin wrapper, because widening a
    return type in place is how PR #266's 8->9 tuple bump broke three
    integration unpack sites.
    """
    now = now or datetime.now(timezone.utc)
    close_http = False
    if http is None:
        http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        close_http = True
    updated = 0
    skipped = 0
    dead_lettered = 0
    selected = 0
    try:
        async with session_factory() as session:
            pending = (
                await session.execute(
                    sa.text(
                        "SELECT id, symbol, timeframe, direction, anchor_close, "
                        "target_ts, validation_attempts "
                        "FROM prediction_validations "
                        "WHERE validated_at IS NULL "
                        "  AND unvalidatable_reason IS NULL "
                        "  AND target_ts <= :now "
                        "ORDER BY target_ts ASC LIMIT :lim"
                    ),
                    {"now": now, "lim": batch_limit},
                )
            ).all()
            selected = len(pending)
            unresolved: list[str] = []

            async def _note_attempt(row: sa.Row, reason: str) -> None:
                """Count the attempt; dead-letter once it is hopeless.

                The threshold is what converts an invisible permanent
                stall into a bounded, attributed write-off.
                """
                nonlocal dead_lettered
                attempts = int(row.validation_attempts or 0) + 1
                if attempts >= MAX_VALIDATION_ATTEMPTS:
                    await session.execute(
                        sa.text(
                            "UPDATE prediction_validations "
                            "SET validation_attempts = :a, last_attempt_at = :t, "
                            "    unvalidatable_reason = :r "
                            "WHERE id = :id"
                        ),
                        {"a": attempts, "t": now,
                         "r": f"{reason} after {attempts} attempts", "id": row.id},
                    )
                    dead_lettered += 1
                    log.error(
                        "validator_dead_letter: id=%s %s/%s target_ts=%s "
                        "reason=%s after %d attempts -- EXCLUDED from further "
                        "selection so it cannot block the queue behind it. "
                        "Re-open with unvalidatable_reason = NULL.",
                        row.id, row.symbol, row.timeframe, row.target_ts,
                        reason, attempts,
                    )
                else:
                    await session.execute(
                        sa.text(
                            "UPDATE prediction_validations "
                            "SET validation_attempts = :a, last_attempt_at = :t "
                            "WHERE id = :id"
                        ),
                        {"a": attempts, "t": now, "id": row.id},
                    )

            for row in pending:
                try:
                    if await _validate_one(session, http, row):
                        updated += 1
                    else:
                        # The silent skip, now counted. This branch is
                        # what ran ~100 times a minute for 53 days while
                        # every heartbeat said "ok".
                        skipped += 1
                        await _note_attempt(row, "close_unavailable")
                except UnresolvedMarket:
                    # Named, not swallowed by the broad handler below. The
                    # reason it gets its own arm is volume: the listing
                    # caches are process-global, so a cold cache makes
                    # EVERY row in the batch unresolvable and the broad
                    # handler would emit up to batch_limit near-identical
                    # warnings every 60s. One aggregated ERROR per pass
                    # names the scale instead, which is the number that
                    # distinguishes "one freshly-listed symbol" from
                    # "prediction validation has stopped".
                    unresolved.append(f"{row.symbol}/{row.timeframe}")
                    await _note_attempt(row, "market_unresolved")
                except Exception as e:  # noqa: BLE001
                    log.warning("validate_one failed for id=%s: %s", row.id, e)
                    skipped += 1
                    await _note_attempt(row, "validate_one_error")
            if unresolved:
                log.error(
                    "validator_market_unresolved: %d of %d pending rows could "
                    "not be placed on a market and stay PENDING (will retry "
                    "next cycle): %s. If this count equals the batch and "
                    "persists, app.workers.futures_only_refresh has never "
                    "completed a cycle and prediction validation is STOPPED, "
                    "not slow. If it is a handful, those symbols were listed "
                    "after the last 24h listing refresh.",
                    len(unresolved), len(pending), ", ".join(unresolved[:10]),
                )
            await session.commit()
    finally:
        if close_http:
            await http.aclose()
    return ValidationPass(
        selected=selected, advanced=updated, skipped=skipped,
        dead_lettered=dead_lettered, unresolved=len(unresolved),
    )


async def validate_pending(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    http: httpx.AsyncClient | None = None,
    now: datetime | None = None,
    batch_limit: int = 100,
) -> int:
    """Run one validator pass. Returns the count of rows updated.

    THIN WRAPPER, kept at its original signature and return type on
    purpose. Every existing caller and test passes through here
    unchanged; :func:`validate_pending_detailed` carries the new
    ``(selected, advanced)`` pair for anything that needs it. Widening
    this return in place is the pitfall that broke three integration
    unpack sites when PR #266 bumped a tuple from 8 to 9.
    """
    result = await validate_pending_detailed(
        session_factory, http=http, now=now, batch_limit=batch_limit,
    )
    return result.advanced


async def _loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_s: float = VALIDATOR_INTERVAL_SECONDS,
) -> None:
    log.info(
        "prediction_validator: starting (interval=%ds, neutral_eps=%.2f%%)",
        int(interval_s), NEUTRAL_EPSILON_PCT,
    )
    while True:
        try:
            res = await validate_pending_detailed(session_factory)
            # BOTH NUMBERS, ALWAYS. The old heartbeat carried only
            # validated_in_tick, so the 53-day stall of 2026-09-03
            # reported status="ok" with validated_in_tick=0 every 60s --
            # which is also exactly what a healthy idle validator
            # reports. `selected` is what separates them.
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error" if res.stalled else "ok",
                details={
                    "validated_in_tick": res.advanced,
                    "selected": res.selected,
                    "advanced": res.advanced,
                    "skipped": res.skipped,
                    "dead_lettered": res.dead_lettered,
                    "unresolved": res.unresolved,
                    "stalled": res.stalled,
                },
            )
            try:
                async with session_factory() as s2:
                    streak = await record_queue_pass(
                        s2, WORKER_NAME,
                        selected=res.selected, advanced=res.advanced,
                    )
                    await s2.commit()
            except Exception as e:  # noqa: BLE001
                # Observability must never take the worker down with it.
                log.warning("validator queue-progress record failed: %s", e)
                streak = 0
            if res.stalled:
                log.error(
                    "validator_STALLED: selected=%d advanced=0 skipped=%d "
                    "dead_lettered=%d unresolved=%d -- consecutive stalled "
                    "passes=%d. Work was available and none of it "
                    "progressed. This is the head-of-line signature; it is "
                    "NOT an idle queue (that reports selected=0).",
                    res.selected, res.skipped, res.dead_lettered,
                    res.unresolved, streak,
                )
            elif res.advanced > 0:
                log.info(
                    "prediction_validator: validated %d of %d selected "
                    "this tick (skipped=%d, dead_lettered=%d)",
                    res.advanced, res.selected, res.skipped,
                    res.dead_lettered,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("prediction_validator tick failed: %s", e)
        await asyncio.sleep(interval_s)


def start_prediction_validator_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(_loop(session_factory))


__all__ = [
    "NEUTRAL_EPSILON_PCT",
    "VALIDATOR_INTERVAL_SECONDS",
    "WORKER_NAME",
    "MAX_VALIDATION_ATTEMPTS",
    "ValidationPass",
    "record_pending_validation",
    "validate_pending",
    "validate_pending_detailed",
    "start_prediction_validator_task",
]
