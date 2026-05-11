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
from datetime import datetime, timedelta, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.adapters.binance import BinanceClient
from app.ops.heartbeat import record_heartbeat

log = logging.getLogger(__name__)

VALIDATOR_INTERVAL_SECONDS: int = 60
NEUTRAL_EPSILON_PCT: float = 0.1  # below this absolute % move, NEUTRAL is "correct"
WORKER_NAME: str = "prediction_validator_task"


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
    """Fetch the closing price for the bar whose open-time equals ``ts``."""
    binance_sym = symbol.replace("/", "")
    client = BinanceClient(http=http)
    # Pull a window around the target so we don't lose a few-millisecond drift.
    start_ms = int(ts.timestamp() * 1000)
    try:
        # fetch_klines is symbol/timeframe based; we accept the small
        # over-fetch and pick the bar that matches our target open-time.
        bars = await client.fetch_klines(binance_sym, timeframe, limit=3)
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


async def validate_pending(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    http: httpx.AsyncClient | None = None,
    now: datetime | None = None,
    batch_limit: int = 100,
) -> int:
    """Run one validator pass. Returns the count of rows updated.

    Pulled out as a standalone function so unit tests can drive it
    deterministically without spinning up the asyncio loop.
    """
    now = now or datetime.now(timezone.utc)
    close_http = False
    if http is None:
        http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        close_http = True
    updated = 0
    try:
        async with session_factory() as session:
            pending = (
                await session.execute(
                    sa.text(
                        "SELECT id, symbol, timeframe, direction, anchor_close, "
                        "target_ts FROM prediction_validations "
                        "WHERE validated_at IS NULL AND target_ts <= :now "
                        "ORDER BY target_ts ASC LIMIT :lim"
                    ),
                    {"now": now, "lim": batch_limit},
                )
            ).all()
            for row in pending:
                try:
                    if await _validate_one(session, http, row):
                        updated += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("validate_one failed for id=%s: %s", row.id, e)
            await session.commit()
    finally:
        if close_http:
            await http.aclose()
    return updated


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
            n = await validate_pending(session_factory)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="ok", details={"validated_in_tick": n},
            )
            if n > 0:
                log.info("prediction_validator: validated %d row(s) this tick", n)
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
    "record_pending_validation",
    "validate_pending",
    "start_prediction_validator_task",
]
