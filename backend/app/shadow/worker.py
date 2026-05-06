"""Shadow trading worker — multi-symbol orchestrator.

Mirrors the live_prediction.py pattern:
- Seed REST history per symbol into in-memory pandas frames (last 1000 bars).
- For each closed candle from the multi-stream:
    * If the symbol has an open position → bump bars_held, check exit; on hit,
      persist the closed trade (hash-chained), delete the open row, set a
      cooldown, then publish.
    * Else → build a fresh prediction; if the PositionGate allows entry, ask
      the SignalEvaluator for a signal; if one is returned, persist the open
      position, then publish.

Persist-before-publish: the audit chain is the source of truth, so we only
publish over the WS once persistence succeeds (mirroring live_prediction.py).
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.ws import manager
from app.core.predictor import _atr, build_prediction
from app.core.scoring import _pattern_stats_cache as pattern_stats_cache
from app.data.adapters.binance import BinanceClient
from app.db.session import get_session_factory
from app.shadow.engine import (
    PositionGate,
    ShadowPosition,
    SignalEvaluator,
)
from app.shadow.exit_monitor import ExitDecision, check_exit
from app.shadow.multi_stream import MultiStreamCandle, MultiStreamReader
from app.shadow.persistence import (
    delete_open_position,
    list_open_positions,
    load_cooldowns,
    persist_closed_trade,
    persist_open_position,
    set_cooldown,
)
from app.shadow.universe import load_current_universe
from app.ws import shadow_updates

log = logging.getLogger(__name__)

# §5.1 spec constants.
SHADOW_POSITION_SIZE_USDT: float = 30.0
COOLDOWN_MINUTES: int = 30
HISTORY_BARS: int = 300
MAX_BUFFERED_BARS: int = 1000
SHADOW_TIMEFRAME: str = "1h"

# SP-0.7: the single shadow worker writes rows on behalf of the bootstrap
# admin (id=1, see migration 0005). SP-8 will spawn one worker per user
# and parameterise this on the worker dataclass instance.
BOOTSTRAP_ADMIN_USER_ID: int = 1


class _StreamReader(Protocol):
    """Anything with an async ``stream()`` that yields MultiStreamCandle."""

    def stream(self) -> Any: ...


@dataclass
class ShadowWorker:
    """Async orchestrator for the shadow trading subsystem."""

    symbols: list[str]
    session_factory: async_sessionmaker[AsyncSession]
    reader: _StreamReader
    # In-memory bar buffers keyed by Binance symbol (uppercase, no slash).
    bars: dict[str, pd.DataFrame] = field(default_factory=dict)
    # In-memory caches reloaded on startup.
    open_positions: dict[str, ShadowPosition] = field(default_factory=dict)
    cooldowns: dict[str, datetime] = field(default_factory=dict)
    # Optional pre-seeded history (test injection point). When supplied,
    # ``setup()`` skips the REST fetch.
    seed_history: dict[str, pd.DataFrame] | None = None
    evaluator: SignalEvaluator = field(default_factory=SignalEvaluator)
    # SP-0.7 single-worker default; SP-8 will populate per spawned user.
    user_id: int = BOOTSTRAP_ADMIN_USER_ID

    async def setup(self) -> None:
        """Load open positions + cooldowns + seed REST history."""
        async with self.session_factory() as session:
            for pos in await list_open_positions(session, user_id=self.user_id):
                self.open_positions[pos.symbol] = pos
            self.cooldowns = await load_cooldowns(session, user_id=self.user_id)

        if self.seed_history is not None:
            for sym, df in self.seed_history.items():
                self.bars[sym] = df.copy()
            return

        async with httpx.AsyncClient() as http:
            client = BinanceClient(http=http)
            for sym in self.symbols:
                try:
                    history = await client.fetch_klines(
                        sym, SHADOW_TIMEFRAME, limit=HISTORY_BARS,
                    )
                except Exception as e:
                    log.warning("seed history failed for %s: %s", sym, e)
                    continue
                if not history:
                    continue
                df = pd.DataFrame([c.__dict__ for c in history])
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
                self.bars[sym] = df

    async def run(self) -> None:
        """Setup + consume the multi-stream until it ends or is cancelled."""
        await self.setup()
        async for candle in self.reader.stream():
            try:
                await self._handle_candle(candle)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("shadow worker handler failed for %s: %s",
                              candle.symbol, e)

    # --- internals -----------------------------------------------------

    def _append_bar(self, candle: MultiStreamCandle) -> pd.DataFrame:
        """Append the closed candle to the in-memory buffer (last N kept)."""
        new_row = pd.DataFrame(
            [[candle.open, candle.high, candle.low, candle.close, candle.volume]],
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([candle.ts], name="ts"),
        )
        existing = self.bars.get(candle.symbol)
        if existing is None or existing.empty:
            buf = new_row
        else:
            buf = pd.concat([existing, new_row]).iloc[-MAX_BUFFERED_BARS:]
        self.bars[candle.symbol] = buf
        return buf

    async def _handle_candle(self, candle: MultiStreamCandle) -> None:
        buf = self._append_bar(candle)

        if candle.symbol in self.open_positions:
            await self._maybe_close_position(candle)
            return

        await self._maybe_open_position(candle, buf)

    async def _maybe_close_position(self, candle: MultiStreamCandle) -> None:
        pos = self.open_positions[candle.symbol]
        pos.bars_held += 1
        pos.last_check_at = candle.ts

        decision: ExitDecision | None = check_exit(
            pos,
            bar_high=candle.high,
            bar_low=candle.low,
            bar_close=candle.close,
        )
        if decision is None:
            return

        inputs_hash = _inputs_hash(candle)
        cooldown_until = candle.ts + timedelta(minutes=COOLDOWN_MINUTES)

        try:
            async with self.session_factory() as session:
                await persist_closed_trade(
                    session, pos,
                    user_id=self.user_id,
                    exit_price=decision.exit_price,
                    exit_reason=decision.reason,
                    closed_at=candle.ts,
                    bars_held=pos.bars_held,
                    inputs_hash=inputs_hash,
                )
                await delete_open_position(
                    session, user_id=self.user_id, symbol=candle.symbol,
                )
                await set_cooldown(
                    session, user_id=self.user_id,
                    symbol=candle.symbol, until=cooldown_until,
                )
                await session.commit()
        except Exception as e:
            log.error("persist close failed for %s; suppressing publish: %s",
                      candle.symbol, e)
            return

        # Update in-memory caches only after the DB commit succeeds.
        self.open_positions.pop(candle.symbol, None)
        self.cooldowns[candle.symbol] = cooldown_until

        pnl_pct, pnl_usdt = _pnl(pos, decision.exit_price)
        await shadow_updates.publish_position_closed(
            manager,
            symbol=candle.symbol,
            direction=pos.direction.value,
            entry_price=pos.entry_price,
            exit_price=decision.exit_price,
            exit_reason=decision.reason.value,
            pnl_pct=pnl_pct,
            pnl_usdt=pnl_usdt,
            bars_held=pos.bars_held,
            signal_id=pos.signal_id,
            closed_at=candle.ts,
        )

    async def _maybe_open_position(
        self, candle: MultiStreamCandle, buf: pd.DataFrame,
    ) -> None:
        gate = PositionGate(
            open_symbols=set(self.open_positions.keys()),
            cooldowns=dict(self.cooldowns),
        )
        if gate.is_blocked(candle.symbol, now=candle.ts):
            return

        # SP-2 Phase E E4: cached PatternStatsLookup keyed by (symbol, timeframe).
        # First closed candle per symbol triggers a single DB read; subsequent
        # candles read from the in-process cache.
        try:
            async with self.session_factory() as stats_session:
                stats_lookup = await pattern_stats_cache.get_or_load(
                    stats_session,
                    symbol=candle.symbol,
                    timeframe=SHADOW_TIMEFRAME,
                )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "pattern_stats lookup failed for %s; running without L2: %s",
                candle.symbol, e,
            )
            stats_lookup = None

        # SP-9 Phase E2: build_prediction is async + may consult news_items
        # via the session. Open a short-lived L9 session per candle.
        try:
            async with self.session_factory() as l9_session:
                pred = await build_prediction(
                    symbol=candle.symbol,
                    timeframe=SHADOW_TIMEFRAME,
                    bars=buf,
                    pattern_stats_lookup=stats_lookup,
                    session=l9_session,
                )
        except Exception as e:
            log.warning("build_prediction failed for %s: %s", candle.symbol, e)
            return

        atr_value = _atr(buf)
        if atr_value <= 0:
            return

        layer_scores = {
            k: (v.model_dump() if v is not None else None)
            for k, v in pred.layer_scores.items()
        }
        signal = self.evaluator.evaluate(
            symbol=candle.symbol,
            score=pred.final.score,
            confidence=pred.final.confidence,
            last_close=candle.close,
            atr=atr_value,
            layer_scores=layer_scores,
            ts=candle.ts,
        )
        if signal is None:
            return

        position = ShadowPosition.from_signal(
            signal, position_size_usdt=SHADOW_POSITION_SIZE_USDT,
        )

        try:
            async with self.session_factory() as session:
                await persist_open_position(
                    session, position, user_id=self.user_id,
                )
                await session.commit()
        except Exception as e:
            log.error("persist open failed for %s; suppressing publish: %s",
                      candle.symbol, e)
            return

        self.open_positions[candle.symbol] = position

        await shadow_updates.publish_position_opened(
            manager,
            symbol=candle.symbol,
            direction=position.direction.value,
            entry_price=position.entry_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            signal_id=position.signal_id,
            score=position.entry_score,
            confidence=position.entry_confidence,
            opened_at=position.opened_at,
        )


def _inputs_hash(candle: MultiStreamCandle) -> str:
    """Mirror predictor._compute_inputs_hash for a single bar.

    sha256 of "{symbol}|{timeframe}|{ts}|O|H|L|C|V".
    """
    canon = (
        f"{candle.symbol}|{candle.timeframe}|{candle.ts.isoformat()}|"
        f"{candle.open}|{candle.high}|{candle.low}|{candle.close}|{candle.volume}"
    )
    return hashlib.sha256(canon.encode()).hexdigest()


def _pnl(pos: ShadowPosition, exit_price: float) -> tuple[float, float]:
    from app.shadow.engine import Direction
    if pos.direction is Direction.LONG:
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
    else:
        pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100.0
    return pnl_pct, pos.position_size_usdt * pnl_pct / 100.0


async def _build_default_worker() -> ShadowWorker:
    """Build a worker using the production session factory and current universe."""
    factory = get_session_factory()
    async with factory() as session:
        universe = await load_current_universe(session)
    symbols = [e.symbol for e in universe]
    if not symbols:
        log.warning("shadow worker: empty universe — using BTCUSDT fallback")
        symbols = ["BTCUSDT"]
    reader = MultiStreamReader(symbols=symbols, timeframe=SHADOW_TIMEFRAME)
    return ShadowWorker(
        symbols=symbols, session_factory=factory, reader=reader,
    )


async def _run_default() -> None:
    worker = await _build_default_worker()
    await worker.run()


def start_shadow_worker() -> asyncio.Task[None]:
    """Spawn the shadow worker as a background task. Called from lifespan."""
    return asyncio.create_task(_run_default())


# Public re-exports — kept tiny on purpose so the FastAPI lifespan doesn't
# need to know any internal types.
__all__ = [
    "COOLDOWN_MINUTES",
    "HISTORY_BARS",
    "SHADOW_POSITION_SIZE_USDT",
    "SHADOW_TIMEFRAME",
    "ShadowWorker",
    "start_shadow_worker",
]


