import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.ws import manager
from app.config import get_settings
from app.core.execution.persistence import persist_prediction
from app.core.gates.entry_quality import evaluate_all_gates
from app.core.predictor import build_prediction
from app.core.scoring import _pattern_stats_cache as pattern_stats_cache
from app.core.scoring.layer8_convlstm import GhostInput
from app.data.adapters.binance import BinanceClient, BinanceKlineStream
from app.db.dispatch_decisions import record_dispatch_decision
from app.db.payload_builders import build_predictions_payload
from app.db.session import get_session_factory
from app.ml.validator import record_pending_validation
from app.ops.heartbeat import record_heartbeat
from app.shadow.multi_stream import MultiStreamCandle
from app.trading.execution.glue import (
    _parse_mtf_adx_by_tf_json,
    dispatch_if_eligible,
    vault_keys,
)

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


# PR1's compute_realized_vol_20d (app/core/scoring/vol_normalization.py)
# requires >= MIN_DAILY_BARS_FOR_VOL (20) distinct calendar days of history
# before it will compute effective_score/realized_vol_20d instead of
# returning None. At the 1h timeframe this worker always runs at, 300 bars
# is only 12.5 days — every backend restart reset the REST seed back below
# the floor, so the aggregator hook needed ~7.5 more days of *uninterrupted*
# uptime to ever populate those two columns. Deploy cadence went from ~1-2
# commits/day to several/day starting 2026-07-14, so no restart since then
# has had that much runway — hence realized_vol_20d/effective_score going
# 100% NULL in `predictions` from that date forward (root-caused 2026-08-05).
# 504 bars = 21 days gives a 1-day safety margin over the 20-day floor so
# the columns populate from the FIRST candle after every restart, with zero
# dependency on inter-deploy uptime.
HISTORY_SEED_BARS: int = 504


async def run_live_prediction(
    symbol_pair: str = "BTC/USDT",
    timeframe: str = "1h",
    *,
    candle_source: AsyncIterator[MultiStreamCandle] | None = None,
    symbol_source: str = "established_top20",
) -> None:
    """Seed REST history, subscribe to Binance WS (or consume an injected
    candle_source), on each closed candle:
    1. Append candle to in-memory DataFrame (last 1000 bars)
    2. Build prediction (compose layers + aggregate)
    3. Persist prediction row to predictions table via audit hash chain
    4. Publish payload over WebSocket so UI updates
    Persist comes BEFORE publish — if persist fails (DB down), do not publish.

    ``candle_source``, when supplied, replaces the default Binance SPOT
    WS subscription — used by the futures-only REST poller (Phase 4) so
    this function's scoring/gating/dispatch/persistence logic is shared,
    not duplicated, between the two candle-delivery mechanisms.
    ``symbol_source`` is cohort metadata threaded into the persisted and
    dispatched payloads; defaults to ``"established_top20"`` so every
    existing caller is unaffected.
    """
    binance_symbol = symbol_pair.replace("/", "")

    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        history = await client.fetch_klines(
            binance_symbol, timeframe, limit=HISTORY_SEED_BARS,
        )
    bars = pd.DataFrame([c.__dict__ for c in history])
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars = bars.set_index("ts")[["open", "high", "low", "close", "volume"]]

    session_factory = get_session_factory()
    # Annotated AsyncIterator[Any]: BinanceKlineStream.stream() actually
    # yields app.core.dataquality.validator.Candle (aliased ValidatorCandle
    # in app.data.adapters.binance), not MultiStreamCandle -- the two are
    # duck-type compatible for every attribute this loop reads (.open,
    # .high, .low, .close, .volume, .ts) but are not the same nominal type,
    # so the local variable needs a looser annotation than the parameter's
    # public AsyncIterator[MultiStreamCandle] contract to type-check both
    # branches. Type-annotation-only change; no runtime effect.
    source: AsyncIterator[Any] | None = candle_source
    if source is None:
        stream = BinanceKlineStream(symbol=binance_symbol, timeframe=timeframe)
        source = stream.stream()

    async for candle in source:
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

        # SP-1: ghost candle prediction (additive to persistence/display;
        # also threaded into scoring below when L8_GHOST_SCORING_ENABLED).
        # `get_active_model_and_checkpoint` returns None when no active ML
        # checkpoint is loaded — in that case we persist + publish exactly
        # as before (ghost columns NULL, payload["ghost"] = None, L8 abstains).
        # Computed BEFORE build_prediction (moved up from its original
        # post-build_prediction position) so the L8 vote can reach the
        # aggregator when the flag is on.
        ghost_payload: dict[str, Any] = {}
        ghost_input: GhostInput | None = None
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
                if get_settings().L8_GHOST_SCORING_ENABLED:
                    ghost_input = GhostInput(
                        ghost_close=ghost.close,
                        ghost_uncertainty=ghost.uncertainty,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "predict_ghost_candle failed: %s; persisting without ghost", e
                )

        # SP-9 Phase E2: build_prediction is async + may consult news_items
        # via the session. We open a short-lived session per candle so the
        # L9 layer can query without holding a session across the WS loop.
        try:
            async with session_factory() as l9_session:
                pred = await build_prediction(
                    symbol=symbol_pair, timeframe=timeframe, bars=bars,
                    pattern_stats_lookup=stats_lookup,
                    session=l9_session,
                    ghost=ghost_input,
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
            symbol_source=symbol_source,
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
            symbol_source=symbol_source,
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
    symbol_source: str = "established_top20",
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
    # PR-BOT-INTELLIGENCE-UPGRADE: extract Layer-2 pattern data from
    # pred.layer_scores so the dispatcher's entry-quality gate can apply
    # the pattern boost/penalty. Key shape is `str(int)` (see
    # `predictor.build_prediction` — `{str(i): _layer_to_out(s) ...}`).
    # None-safe — if L2 abstained (slot missing or value None), both
    # extracted fields stay None and the gate's pattern check is a no-op.
    _l2 = getattr(pred, "layer_scores", {}).get("2") if hasattr(pred, "layer_scores") else None
    _l2_direction: str | None = getattr(_l2, "direction", None) if _l2 is not None else None
    _l2_confidence: float | None = getattr(_l2, "confidence", None) if _l2 is not None else None
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
                    # PR-PLUMBING-1 Fix 1: thread the predictor's per-day
                    # funding rate so the dispatcher's funding-rate
                    # kill-switch evaluates real values instead of 0.0.
                    # None when the predictor's intermarket lookup failed —
                    # glue's `or 0.0` collapse keeps the gate fail-open.
                    "pred_funding_rate_daily": pred.funding_rate_daily,
                    # PR-BOT-INTELLIGENCE-UPGRADE: thread pattern + ADX
                    # context for the extended entry-quality gate. All
                    # three are Optional — the gate sub-checks are flag-
                    # gated and short-circuit when their flag is off.
                    "layer2_direction": _l2_direction,
                    "layer2_confidence": _l2_confidence,
                    "mtf_adx_by_tf_json": getattr(pred, "mtf_adx_by_tf_json", None),
                    # Phase 4 Task 9: cohort tag, threaded all the way to
                    # proposal_from_prediction -> SignalProposal -> the
                    # telegram_signals / live_trades write sites.
                    "symbol_source": symbol_source,
                },
                # PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP (2026-05-26): thread
                # the live worker's session_factory through so
                # _place_live_order can open independent sessions for
                # the pending->open lifecycle (Phase 1 INSERT, Phase 3
                # UPDATE) without holding `dispatch_session` open across
                # the multi-second Binance round-trip.
                session_factory=session_factory,
            )
            await dispatch_session.commit()
        if result is not None:
            log.info(
                "dispatch %s/%s -> %s: %s",
                pred.symbol, pred.timeframe,
                result.outcome, result.detail,
            )
            # Item 3 (2026-08-14): persisted decision log. Fires AFTER the
            # real dispatch decision above is made and (if applicable)
            # acted on -- nothing here can block or alter a trade. Own
            # try/except with a distinct error marker (not the generic
            # "dispatch_if_eligible failed" below) so a telemetry failure
            # is never mistaken for a real dispatch failure, and never
            # trips the healer C1 dispatch-exception aggregation meant
            # for actual trading-path errors.
            try:
                _eq_settings = get_settings()
                _market_regime: str | None = None
                if getattr(_eq_settings, "REGIME_GATE_ENABLED", False):
                    from app.core.regime import get_cached_market_regime
                    _market_regime = await get_cached_market_regime()
                _gate_signal = SimpleNamespace(
                    direction=pred.final.direction,
                    entry_score=pred.final.score,
                    layer2_direction=_l2_direction,
                    layer2_confidence=_l2_confidence,
                    mtf_dominant_tf=pred.mtf_dominant_tf,
                    mtf_adx_by_tf=_parse_mtf_adx_by_tf_json(
                        getattr(pred, "mtf_adx_by_tf_json", None),
                    ),
                )
                gate_verdicts = evaluate_all_gates(
                    _gate_signal, _eq_settings, market_regime=_market_regime,
                )
                await record_dispatch_decision(
                    session_factory,
                    symbol=pred.symbol, timeframe=pred.timeframe, ts=pred.ts,
                    direction=pred.final.direction, final_score=pred.final.score,
                    gate_verdicts=gate_verdicts,
                    outcome=result.outcome, detail=result.detail,
                )
            except Exception as log_exc:  # noqa: BLE001
                log.error(
                    "dispatch_decision_log_failed: symbol=%s timeframe=%s -- %s",
                    pred.symbol, pred.timeframe, log_exc,
                )
    except Exception as e:  # noqa: BLE001
        log.error("dispatch_if_eligible failed: %s", e)
        # Healer C1 writer (Phase 0 completion): record the exception so
        # the C1 detector can aggregate by type and alarm CRITICAL on any
        # NEVER-BEFORE-SEEN class. This is the true outermost boundary —
        # every dispatch-path exception (glue, dispatcher, place-order,
        # audit chain, ANY of it) lands here before being swallowed.
        # Best-effort: healer write failure must never re-raise.
        try:
            from app.healer import record_dispatch_error
            await record_dispatch_error(
                session_factory,
                exception=e,
                context={
                    "symbol": getattr(pred, "symbol", None),
                    "timeframe": getattr(pred, "timeframe", None),
                    "direction": getattr(
                        getattr(pred, "final", None), "direction", None,
                    ),
                },
            )
        except Exception as heal_exc:  # noqa: BLE001
            log.warning(
                "healer.record_dispatch_error failed: %s", heal_exc,
            )


def start_background_worker() -> asyncio.Task:
    return asyncio.create_task(run_live_prediction())
