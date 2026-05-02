import asyncio
import json
import logging

import httpx
import pandas as pd

from app.api.routes.ws import manager
from app.core.execution.persistence import persist_prediction
from app.core.predictor import build_prediction
from app.data.adapters.binance import BinanceClient, BinanceKlineStream
from app.db.session import get_session_factory

log = logging.getLogger(__name__)


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

        try:
            pred = build_prediction(symbol=symbol_pair, timeframe=timeframe, bars=bars)
        except Exception as e:  # noqa: BLE001
            log.warning("build_prediction failed: %s", e)
            continue

        # Persist BEFORE publishing — audit chain is the source of truth.
        try:
            async with session_factory() as session:
                await persist_prediction(session, {
                    "symbol": pred.symbol,
                    "timeframe": pred.timeframe,
                    "ts": pred.ts.isoformat(),
                    "layer_scores": json.dumps({
                        k: (v.model_dump() if v else None)
                        for k, v in pred.layer_scores.items()
                    }),
                    "final_score": pred.final.score,
                    "direction": pred.final.direction,
                    "confidence": pred.final.confidence,
                    "inputs_hash": pred.inputs_hash,
                    "model_version": "sp-0",
                    "cold_start": pred.cold_start,
                })
                await session.commit()
        except Exception as e:  # noqa: BLE001
            log.error("persist_prediction failed; suppressing publish: %s", e)
            continue

        await manager.publish(
            channel="live_prediction",
            key={"symbol": symbol_pair, "timeframe": timeframe},
            payload=pred.model_dump(mode="json"),
        )


def start_background_worker() -> asyncio.Task:
    return asyncio.create_task(run_live_prediction())
