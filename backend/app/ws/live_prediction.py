import asyncio
import json
import logging
from typing import Any

import httpx
import pandas as pd

from app.api.routes.ws import manager
from app.core.execution.persistence import persist_prediction
from app.core.predictor import build_prediction
from app.core.scoring import _pattern_stats_cache as pattern_stats_cache
from app.data.adapters.binance import BinanceClient, BinanceKlineStream
from app.db.session import get_session_factory

log = logging.getLogger(__name__)

# SP-0.7: the singleton live-prediction worker writes rows on behalf of the
# bootstrap admin (id=1, see migration 0005). SP-8 will fan out per user.
BOOTSTRAP_ADMIN_USER_ID: int = 1


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
        try:
            async with session_factory() as session:
                # SP-5 Phase F1: merge prediction_extras (traps_fired, tier,
                # raw scores, multipliers, final) into the persisted JSONB so
                # downstream backtest replays + audit can recover full state.
                _layer_payload: dict[str, Any] = {
                    k: (v.model_dump() if v else None)
                    for k, v in pred.layer_scores.items()
                }
                if pred.prediction_extras is not None:
                    _layer_payload.update(pred.prediction_extras)
                await persist_prediction(session, {
                    "user_id": BOOTSTRAP_ADMIN_USER_ID,
                    "symbol": pred.symbol,
                    "timeframe": pred.timeframe,
                    "ts": pred.ts.isoformat(),
                    "layer_scores": json.dumps(_layer_payload),
                    "final_score": pred.final.score,
                    "direction": pred.final.direction,
                    "confidence": pred.final.confidence,
                    "inputs_hash": pred.inputs_hash,
                    "model_version": "sp-0",
                    "cold_start": pred.cold_start,
                    **ghost_payload,
                })
                await session.commit()
        except Exception as e:  # noqa: BLE001
            log.error("persist_prediction failed; suppressing publish: %s", e)
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


def start_background_worker() -> asyncio.Task:
    return asyncio.create_task(run_live_prediction())
