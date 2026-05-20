import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.ws import manager
from app.config import get_settings
from app.core.execution.persistence import persist_prediction
from app.core.predictor import build_prediction
from app.core.scoring import _pattern_stats_cache as pattern_stats_cache
from app.data.adapters.binance import BinanceClient, BinanceKlineStream
from app.db.payload_builders import build_predictions_payload
from app.db.session import get_session_factory
from app.ml.validator import record_pending_validation
from app.ops.heartbeat import record_heartbeat
from app.trading.execution.glue import dispatch_if_eligible, vault_keys

log = logging.getLogger(__name__)

# FU-1: heartbeat name MUST match worker_registry.py's entry.
WORKER_NAME: str = "live_worker"

# SP-0.7: the singleton live-prediction worker writes rows on behalf of the
# bootstrap admin (id=1, see migration 0005). SP-8 will fan out per user.
BOOTSTRAP_ADMIN_USER_ID: int = 1


async def _persist_prediction_and_schedule_validation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    predictions_payload: dict[str, Any],
    user_id: int,
    symbol: str,
    timeframe: str,
    direction: str,
    score: float,
    confidence: float,
    anchor_ts: datetime,
    anchor_close: float,
) -> int:
    """Persist the prediction in transaction 1, schedule its pending-validation
    row in transaction 2 (best-effort). Returns the new prediction's id.

    Two-session pattern is the 2026-05-17 hotfix for a 6-day production
    silence: pre-hotfix, both writes lived in the same session, and a
    ``NotNullViolationError`` from the validator's INSERT (caused by
    ``prediction_id=None`` being hardcoded at the call site) put the
    Postgres transaction into the aborted state. The subsequent
    ``session.commit()`` failed and ``__aexit__`` rolled back the
    prediction insert. Net effect: ``build_prediction`` ran, the
    aggregator-hook log line fired, but zero rows persisted.

    Splitting into two sessions isolates the writes: validator failure
    can no longer poison the prediction's transaction. The validator is
    explicitly best-effort per its docstring; this code matches that
    contract.
    """
    # --- Transaction 1: persist the prediction (source-of-truth write) ---
    async with session_factory() as session:
        pred_id, _row_hash = await persist_prediction(session, predictions_payload)
        await session.commit()

    # --- Transaction 2: best-effort validator record ---
    try:
        async with session_factory() as session:
            await record_pending_validation(
                session,
                prediction_id=pred_id,
                user_id=user_id,
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                score=score,
                confidence=confidence,
                anchor_ts=anchor_ts,
                anchor_close=anchor_close,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        log.warning(
            "record_pending_validation failed; prediction %s persisted ok: %s",
            pred_id, exc,
        )

    return pred_id


async def run_live_prediction(symbol_pair: str = "BTC/USDT", timeframe: str = "1h") -> None:
    """Seed REST history, subscribe to Binance WS, on each closed candle:
    1. Append candle to in-memory DataFrame (last 1000 bars)
    2. Build prediction (compose layers + aggregate)
    3. Persist prediction row to predictions table via audit hash chain
    4. Publish payload over WebSocket so UI updates
    Persist comes BEFORE publish — if persist fails (DB down), do not publish.
    """
    binance_symbol = symbol_pair.replace("/", "")

    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        history = await client.fetch_klines(binance_symbol, timeframe, limit=300)
    bars = pd.DataFrame([c.__dict__ for c in history])
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars = bars.set_index("ts")[["open", "high", "low", "close", "volume"]]

    session_factory = get_session_factory()
    stream = BinanceKlineStream(symbol=binance_symbol, timeframe=timeframe)

    async for candle in stream.stream():
        new_row = pd.DataFrame(
            [[candle.open, candle.high, candle.low, candle.close, candle.volume]],
            columns=["open", "high", "low", "close", "volume"],
            index=[candle.ts],
        )
        bars = pd.concat([bars, new_row]).iloc[-1000:]

        # SP-2 Phase E E4: load PatternStatsLookup once per (symbol, timeframe)
        # so the L2 aggregator can run on every closed candle without an extra
        # DB round-trip. Cache miss path opens a short-lived session.
        try:
            async with session_factory() as stats_session:
                stats_lookup = await pattern_stats_cache.get_or_load(
                    stats_session, symbol=symbol_pair, timeframe=timeframe,
                )
        except Exception as e:  # noqa: BLE001
            log.warning("pattern_stats lookup failed; running without L2: %s", e)
            stats_lookup = None

        # SP-9 Phase E2: build_prediction is async + may consult news_items
        # via the session. We open a short-lived session per candle so the
        # L9 layer can query without holding a session across the WS loop.
        try:
            async with session_factory() as l9_session:
                pred = await build_prediction(
                    symbol=symbol_pair, timeframe=timeframe, bars=bars,
                    pattern_stats_lookup=stats_lookup,
                    session=l9_session,
                )
        except Exception as e:  # noqa: BLE001
            log.warning("build_prediction failed: %s", e)
            # FU-1 follow-up: error heartbeat so a sustained build_prediction
            # failure (model crash, layer assertion, etc.) surfaces as
            # last_status='error' instead of letting beat_at go stale — the
            # WS loop is still alive but it's not producing predictions.
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error",
                details={
                    "stage": "build_prediction",
                    "symbol": symbol_pair, "timeframe": timeframe,
                    "error": str(e)[:200],
                },
            )
            continue

        # SP-1: ghost candle prediction (additive, never blocks).
        # `get_active_model_and_checkpoint` returns None when no active ML
        # checkpoint is loaded — in that case we persist + publish exactly
        # as before (ghost columns NULL, payload["ghost"] = None).
        ghost_payload: dict[str, Any] = {}
        try:
            from app.ml.checkpoints import get_active_model_and_checkpoint
            from app.ml.inference import predict_ghost_candle

            active = get_active_model_and_checkpoint()
        except ImportError:  # pragma: no cover — checkpoints module not present yet
            active = None

        if active is not None and len(bars) >= 256:
            model, checkpoint = active
            try:
                ghost = predict_ghost_candle(
                    model=model,
                    bars=bars,
                    last_close=float(bars["close"].iloc[-1]),
                )
                ghost_payload = {
                    "ghost_open": ghost.open,
                    "ghost_high": ghost.high,
                    "ghost_low": ghost.low,
                    "ghost_close": ghost.close,
                    "ghost_p5_low": ghost.p5_low,
                    "ghost_p95_high": ghost.p95_high,
                    "ghost_uncertainty": ghost.uncertainty,
                    "model_checkpoint_id": checkpoint.id,
                }
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "predict_ghost_candle failed: %s; persisting without ghost", e
                )

        # Persist BEFORE publishing — audit chain is the source of truth.
        # SP-5 Phase F1: merge prediction_extras (traps_fired, tier, raw scores,
        # multipliers, final) into the persisted JSONB so downstream backtest
        # replays + audit can recover full state. Build _layer_payload at the
        # call site so it stays available for _maybe_dispatch below without a
        # JSON round-trip. ts is a datetime, NOT isoformat() string — asyncpg's
        # PostgreSQL TIMESTAMPTZ binding rejects strings; SQLite tests bind
        # datetime->TEXT automatically. SP-1.1 hotfix.
        _layer_payload: dict[str, Any] = {
            k: (v.model_dump() if v else None)
            for k, v in pred.layer_scores.items()
        }
        if pred.prediction_extras is not None:
            _layer_payload.update(pred.prediction_extras)
        _predictions_payload = build_predictions_payload(
            pred,
            user_id=BOOTSTRAP_ADMIN_USER_ID,
            layer_payload=_layer_payload,
            ghost_payload=ghost_payload if ghost_payload else None,
            # PR1 Phase 5 — pass through the 7 record-only fields from pred
            # (populated by the aggregator hook added in Task 3.4;
            # None when hook returns no data, which is fail-open safe).
            mtf_agreement=pred.mtf_agreement,
            mtf_dominant_tf=pred.mtf_dominant_tf,
            mtf_directions_json=pred.mtf_directions_json,
            p_win=pred.p_win,
            effective_score=pred.effective_score,
            realized_vol_20d=pred.realized_vol_20d,
            funding_directional_adj=pred.funding_directional_adj,
        )

        # 2026-05-17 HOTFIX: two-session pattern. The validator INSERT lives
        # in its OWN transaction so a NotNullViolationError (or any other
        # validator-side error) cannot poison the prediction's transaction
        # and roll back the prediction insert. Pre-hotfix this exact pattern
        # silently lost ~6 days of predictions in prod. See
        # docs/superpowers/specs/2026-05-17-hotfix-validator-transaction-scope.md
        # and tests/integration/test_live_prediction_validator_isolation.py.
        try:
            await _persist_prediction_and_schedule_validation(
                session_factory,
                predictions_payload=_predictions_payload,
                user_id=BOOTSTRAP_ADMIN_USER_ID,
                symbol=pred.symbol,
                timeframe=pred.timeframe,
                direction=pred.final.direction,
                score=pred.final.score,
                confidence=pred.final.confidence,
                anchor_ts=pred.ts,
                anchor_close=float(bars["close"].iloc[-1]),
            )
        except Exception as e:  # noqa: BLE001
            # Only reached if the prediction itself failed to persist (e.g.,
            # DB unreachable, hash chain assertion). Validator failures are
            # absorbed inside the helper.
            log.error("persist_prediction failed; suppressing publish: %s", e)
            # FU-1 follow-up: error heartbeat — the 2026-05-17 hotfix scenario
            # (validator-poisoned transaction) presents here. We want the
            # watchdog to see 'error' immediately rather than wait for
            # max_staleness_seconds.
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error",
                details={
                    "stage": "persist_prediction",
                    "symbol": symbol_pair, "timeframe": timeframe,
                    "error": str(e)[:200],
                },
            )
            continue

        # Extend WS payload with ghost (None when no active model).
        payload = pred.model_dump(mode="json")
        if ghost_payload:
            payload["ghost"] = {
                "open": ghost_payload["ghost_open"],
                "high": ghost_payload["ghost_high"],
                "low": ghost_payload["ghost_low"],
                "close": ghost_payload["ghost_close"],
                "p5_low": ghost_payload["ghost_p5_low"],
                "p95_high": ghost_payload["ghost_p95_high"],
                "uncertainty": ghost_payload["ghost_uncertainty"],
            }
        else:
            payload["ghost"] = None
        await manager.publish(
            channel="live_prediction",
            key={"symbol": symbol_pair, "timeframe": timeframe},
            payload=payload,
        )

        # SP-8 Phase J: dispatch the signal (Telegram message or live
        # order) when autonomous trading is enabled. _maybe_dispatch
        # gates on vault_keys() + valid trade_setup; everything else
        # logs and swallows so a dispatch hiccup never breaks the
        # candle loop.
        await _maybe_dispatch(
            session_factory, pred=pred, layer_payload=_layer_payload,
        )

        # FU-1: heartbeat after each fully-processed candle. The watchdog
        # reads worker_heartbeats.beat_at to detect silent failures. Failure
        # to reach this point (exception escaping any of the try blocks
        # above, WS stream stall, etc.) means no fresh heartbeat → watchdog
        # alarms after max_staleness_seconds. record_heartbeat is best-effort
        # wrapped — never raises.
        await record_heartbeat(
            session_factory, WORKER_NAME,
            status="ok",
            details={"symbol": symbol_pair, "timeframe": timeframe},
        )


async def _maybe_dispatch(
    session_factory: Any, *, pred: Any, layer_payload: dict[str, Any],
) -> None:
    """Bridge between the live-prediction loop and the execution glue.

    Skips silently when the vault isn't loaded (autonomous trading off)
    or the prediction has no usable trade setup (NEUTRAL signal). Any
    dispatch error is logged + swallowed — the candle loop must keep
    ticking even when Binance / Telegram are sad.

    Extracted from the worker body so unit tests can drive it directly
    instead of standing up a full WS stream.
    """
    if vault_keys() is None:
        return
    ts = pred.trade_setup
    if ts is None or ts.entry is None or ts.stop_loss is None or ts.take_profit is None:
        return
    try:
        async with session_factory() as dispatch_session:
            result = await dispatch_if_eligible(
                dispatch_session,
                user_id=BOOTSTRAP_ADMIN_USER_ID,
                use_testnet=get_settings().binance_use_testnet,
                proposal_kwargs={
                    "symbol": pred.symbol,
                    "timeframe": pred.timeframe,
                    "pred_direction": pred.final.direction,
                    "pred_confidence": pred.final.confidence,
                    "layer_summary": layer_payload,
                    "inputs_hash": pred.inputs_hash,
                    "entry_price": ts.entry,
                    "stop_loss_price": ts.stop_loss,
                    "take_profit_price": ts.take_profit,
                    # PR2: thread MTF fields from the prediction so the
                    # dispatcher gate can read them. Each is Optional —
                    # the proposal_from_prediction parser fails open on
                    # malformed JSON. PR1 populated these on the
                    # LivePredictionOut record; we forward them here.
                    "mtf_agreement": pred.mtf_agreement,
                    "mtf_dominant_tf": pred.mtf_dominant_tf,
                    "mtf_directions_json": pred.mtf_directions_json,
                    # PR-strategy-1: thread the aggregator's signed final
                    # score so the dispatcher's entry-quality gate can
                    # apply the LONG threshold.
                    "pred_score": pred.final.score,
                },
            )
            await dispatch_session.commit()
        if result is not None:
            log.info(
                "dispatch %s/%s -> %s: %s",
                pred.symbol, pred.timeframe,
                result.outcome, result.detail,
            )
    except Exception as e:  # noqa: BLE001
        log.error("dispatch_if_eligible failed: %s", e)


def start_background_worker() -> asyncio.Task:
    return asyncio.create_task(run_live_prediction())
