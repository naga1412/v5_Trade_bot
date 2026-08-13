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
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.ws import manager
from app.config import get_settings
from app.core.gates.entry_quality import AllowDecision, open_position_gate
from app.core.predictor import _atr, build_prediction
from app.core.scoring import _pattern_stats_cache as pattern_stats_cache
from app.core.scoring.layer8_convlstm import GhostInput
from app.data.adapters.binance import BinanceClient
from app.db.session import get_session_factory
from app.ops.heartbeat import record_heartbeat
from app.shadow.engine import (
    PositionGate,
    ShadowPosition,
    SignalEvaluator,
)
from app.shadow.exit_monitor import ExitDecision, ExitReason, check_exit
from app.shadow.multi_stream import MultiStreamCandle, MultiStreamReader
from app.shadow.observation import build_obs_components, persist_observation
from app.shadow.persistence import (
    delete_open_position,
    insert_shadow_trade_variant,
    list_open_positions,
    lookup_shadow_trade_id_by_row_hash,
    persist_closed_trade,
    persist_open_position,
    set_cooldown,
)
from app.shadow.universe import load_shadow_universe
from app.ws import shadow_updates

log = logging.getLogger(__name__)

# §5.1 spec constants.
SHADOW_POSITION_SIZE_USDT: float = 30.0
COOLDOWN_MINUTES: int = 240  # 4 h — prevents repeat stop-outs on the same choppy signal
HISTORY_BARS: int = 504  # 21 days of 1h bars — compute_realized_vol_20d's floor (PR #400 parity)
MAX_BUFFERED_BARS: int = 1000
SHADOW_TIMEFRAME: str = "1h"

# SP-0.7: the single shadow worker writes rows on behalf of the bootstrap
# admin (id=1, see migration 0005). SP-8 will spawn one worker per user
# and parameterise this on the worker dataclass instance.
BOOTSTRAP_ADMIN_USER_ID: int = 1


class _StreamReader(Protocol):
    """Anything with an async ``stream()`` that yields MultiStreamCandle."""

    def stream(self) -> Any: ...


@dataclass(frozen=True)
class _EntryQualitySignal:
    """Duck-typed adapter exposing the two attrs `open_position_gate` reads.

    ShadowSignal carries `.score` not `.entry_score`, so the worker wraps
    it here. The gate module stays uniform across both call sites (shadow
    worker + dispatcher), each of which feeds it a differently-shaped
    object.
    """

    direction: str
    entry_score: float


@dataclass
class ShadowWorker:
    """Async orchestrator for the shadow trading subsystem.

    PR3 made this TF-aware. The internal `bars`, `open_positions`, and
    `cooldowns` dicts are keyed by `(symbol, timeframe)` tuples so a
    single worker instance can run multiple TF lanes concurrently. Pre-PR3
    callers (no `timeframes` kwarg) get a single-element ["1h"] list and
    behave bit-identically to the old single-TF worker.
    """

    symbols: list[str]
    session_factory: async_sessionmaker[AsyncSession]
    reader: _StreamReader
    # PR3: when the worker drives ONE TF (legacy single-reader path), the
    # `reader` field is the stream; when it drives MULTIPLE TFs (Phase 4.2
    # wiring), the field on each TF lives in `readers` and the top-level
    # `reader` is the 1h reader for backward-compat with existing tests
    # that pass it directly. `readers` is constructed in __post_init__
    # when more than one TF is requested.
    timeframes: list[str] = field(default_factory=lambda: ["1h"])
    readers: dict[str, _StreamReader] = field(default_factory=dict)
    # PR3: bar buffers keyed by (symbol, timeframe) — was: symbol only.
    bars: dict[tuple[str, str], pd.DataFrame] = field(default_factory=dict)
    # PR3: open positions keyed by (symbol, timeframe) — the PR3 motivation.
    # A 1h BTC position no longer blocks a 15m BTC open on the same symbol.
    open_positions: dict[tuple[str, str], ShadowPosition] = field(default_factory=dict)
    # PR3: cooldowns keyed by (symbol, timeframe).
    cooldowns: dict[tuple[str, str], datetime] = field(default_factory=dict)
    # Optional pre-seeded history (test injection point). When supplied,
    # ``setup()`` skips the REST fetch. Keys are tuples post-PR3, but for
    # backwards-compat with pre-PR3 fixtures, plain-string symbol keys are
    # auto-coerced to (symbol, '1h') tuples in setup().
    seed_history: dict[Any, pd.DataFrame] | None = None
    evaluator: SignalEvaluator = field(default_factory=SignalEvaluator)
    # SP-0.7 single-worker default; SP-8 will populate per spawned user.
    user_id: int = BOOTSTRAP_ADMIN_USER_ID

    def __post_init__(self) -> None:
        # Default-init: when only one TF and no explicit readers, register
        # the passed reader under that TF so the multi-TF run() path works
        # uniformly. Tests that pass a single `reader=` continue to work.
        if not self.readers and self.timeframes:
            self.readers = {self.timeframes[0]: self.reader}

    async def setup(self) -> None:
        """Load open positions + cooldowns + seed REST history.

        PR3: per-(symbol, timeframe) state. Each TF's bar buffer is
        seeded independently. The MTF cache reuse from spec §4.3 lands
        in Phase 4.3 (this method's REST-fetch path is preserved here
        as the fallback).
        """
        log.info(
            "shadow_worker: setup begin (symbols=%d timeframes=%s)",
            len(self.symbols), self.timeframes,
        )
        async with self.session_factory() as session:
            # PR3: list_open_positions returns positions with .timeframe set;
            # key by (symbol, tf) in the in-memory cache.
            for pos in await list_open_positions(session, user_id=self.user_id):
                self.open_positions[(pos.symbol, pos.timeframe)] = pos
            # PR3: load_cooldowns_per_tf returns dict[(sym, tf), datetime].
            from app.shadow.persistence import load_cooldowns_per_tf
            self.cooldowns = await load_cooldowns_per_tf(
                session, user_id=self.user_id,
            )
        log.info(
            "shadow_worker: loaded %d open positions, %d cooldowns from db",
            len(self.open_positions), len(self.cooldowns),
        )

        if self.seed_history is not None:
            # Accept legacy dict[str, DataFrame] (pre-PR3 test fixtures) by
            # coercing string keys to (symbol, '1h') tuples. PR3-aware
            # fixtures can pass dict[tuple[str, str], DataFrame] directly.
            for key, df in self.seed_history.items():
                tuple_key: tuple[str, str]
                if isinstance(key, tuple):
                    tuple_key = (str(key[0]), str(key[1]))
                else:
                    tuple_key = (str(key), self.timeframes[0])
                self.bars[tuple_key] = df.copy()
            log.info(
                "shadow_worker: setup done (seeded from injection: %d buffers)",
                len(self.bars),
            )
            return

        # PR3 Phase 4.3 / spec §4.3 (D1-D3): reuse PR1's MTF kline cache
        # when it's warm. Single source of truth for (symbol, tf) klines
        # across PR1's MTF compute and PR3's shadow seed. On cache miss,
        # REST-fetch and best-effort populate the MTF cache so subsequent
        # MTF compute calls hit warm. mtf_confluence._cache_get returns
        # `list[list[Any]]` (raw Binance kline rows) or None.
        from app.config import get_settings as _get_settings_for_setup
        from app.core.scoring.mtf_confluence import _cache_get, _cache_set
        import time as _time

        # Spec §4.3 D1: cache-hit threshold is SHADOW_PREWARM_BARS, NOT
        # HISTORY_BARS. PR1's prewarm caches 200 klines (MTF compute's
        # need); HISTORY_BARS=504 is the REST-fetch target (21 days of 1h
        # bars, compute_realized_vol_20d's floor — see item 2, 2026-08-13).
        # Using SHADOW_PREWARM_BARS here ensures any cache entry from PR1
        # prewarm is reusable. The rolling buffer accumulates to ~504
        # as live candles flow in (spec §4.3 D2).
        _prewarm_bars = _get_settings_for_setup().SHADOW_PREWARM_BARS

        cache_hits = 0
        rest_fetched = 0
        async with httpx.AsyncClient() as http:
            client = BinanceClient(http=http)
            for tf in self.timeframes:
                for sym in self.symbols:
                    # 1. Try the MTF cache first (raw kline rows).
                    cached_klines = _cache_get(sym, tf)
                    if cached_klines is not None and len(cached_klines) >= _prewarm_bars:
                        df = _klines_to_dataframe(cached_klines)
                        self.bars[(sym, tf)] = df
                        cache_hits += 1
                        continue

                    # 2. Cache miss → REST-fetch via the worker's existing
                    #    Candle-based client. Convert + seed bars buffer.
                    try:
                        history = await client.fetch_klines(
                            sym, tf, limit=HISTORY_BARS,
                        )
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code in (400, 404):
                            log.debug(
                                "seed skip %s/%s (not on Binance Spot): %s",
                                sym, tf, e.response.status_code,
                            )
                        else:
                            log.warning(
                                "seed history failed for %s/%s: %s", sym, tf, e,
                            )
                        continue
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "seed history failed for %s/%s: %s", sym, tf, e,
                        )
                        continue
                    if not history:
                        continue
                    df = pd.DataFrame([c.__dict__ for c in history])
                    df["ts"] = pd.to_datetime(df["ts"], utc=True)
                    df = df.set_index("ts")[
                        ["open", "high", "low", "close", "volume"]
                    ]
                    self.bars[(sym, tf)] = df
                    rest_fetched += 1

                    # 3. Best-effort populate the MTF cache so PR1's compute
                    #    hits warm on the next call. Failures here don't
                    #    block worker setup — log + continue.
                    try:
                        raw_klines = _candles_to_raw_klines(history)
                        _cache_set(sym, tf, raw_klines, fetched_at=_time.time())
                    except Exception as e:  # noqa: BLE001
                        log.debug(
                            "MTF cache populate failed for %s/%s: %s", sym, tf, e,
                        )
        log.info(
            "shadow_worker: setup done (%d cache-hit, %d REST-fetched buffers)",
            cache_hits, rest_fetched,
        )

    async def run(self) -> None:
        """Setup + consume one or more streams until they end or are cancelled.

        PR3: when ``timeframes`` has multiple entries, spawns one consumer
        task per TF via ``asyncio.gather``. Single-TF (legacy) path is
        preserved: one TF → one reader → one consumer.
        """
        await self.setup()
        log.info(
            "shadow_worker: entering stream loop (%d TF(s): %s)",
            len(self.timeframes), self.timeframes,
        )
        tasks = [
            asyncio.create_task(
                self._consume_one_tf(tf, self.readers.get(tf, self.reader)),
            )
            for tf in self.timeframes
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def _consume_one_tf(
        self, tf: str, reader: _StreamReader,
    ) -> None:
        """Consume candles from one TF's reader; route each to _handle_candle.

        Per-TF error handling: a crash here only affects this TF's stream;
        other TFs continue consuming. Cancellation propagates up to the
        gather() in run().

        The error-path heartbeat is itself wrapped so a heartbeat failure
        (DB blip, session_factory hiccup, anything) can't propagate up
        and tear down this consumer — which `run()`'s gather() would
        then cascade into cancelling the other TF tasks. Per-TF
        isolation is the contract the docstring promises.
        """
        async for candle in reader.stream():
            try:
                await self._handle_candle(candle, tf=tf)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception(
                    "shadow worker handler failed for %s/%s: %s",
                    candle.symbol, tf, e,
                )
                # FU-1 follow-up: error heartbeat. record_heartbeat is
                # already best-effort wrapped at the helper level, but
                # belt-and-braces a second try/except here so even an
                # unexpected propagation (e.g. session_factory misbehaving
                # before record_heartbeat's own try-block engages) can't
                # cancel sibling TF consumers via gather().
                try:
                    await record_heartbeat(
                        self.session_factory, "shadow_worker",
                        status="error",
                        details={
                            "symbol": candle.symbol, "tf": tf,
                            "error": str(e)[:200],
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as hb_err:  # noqa: BLE001
                    log.warning(
                        "shadow_worker error-heartbeat itself failed for "
                        "%s/%s; suppressing to preserve per-TF isolation: %s",
                        candle.symbol, tf, hb_err,
                    )

    # --- internals -----------------------------------------------------

    def _append_bar(
        self, candle: MultiStreamCandle, tf: str,
    ) -> pd.DataFrame:
        """Append the closed candle to the (symbol, tf) buffer (last N kept)."""
        new_row = pd.DataFrame(
            [[candle.open, candle.high, candle.low, candle.close, candle.volume]],
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([candle.ts], name="ts"),
        )
        key = (candle.symbol, tf)
        existing = self.bars.get(key)
        if existing is None or existing.empty:
            buf = new_row
        else:
            buf = pd.concat([existing, new_row]).iloc[-MAX_BUFFERED_BARS:]
        self.bars[key] = buf
        return buf

    async def _handle_candle(
        self, candle: MultiStreamCandle, tf: str | None = None,
    ) -> None:
        """Process one closed candle for one TF.

        ``tf`` defaults to ``self.timeframes[0]`` so pre-PR3 callers that
        invoke _handle_candle(candle) without the kwarg continue to work
        with the legacy 1h path. Phase 4.2's run() always passes tf
        explicitly per consumer.
        """
        if tf is None:
            tf = self.timeframes[0]
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug(
                "shadow_worker: paused, skipping candle %s/%s",
                candle.symbol, tf,
            )
            await record_heartbeat(
                self.session_factory, "shadow_worker",
                status="ok", details={"paused": True},
            )
            return
        buf = self._append_bar(candle, tf)

        # W3: keep the module-level BTC close cache fresh so alt symbols
        # processed after BTCUSDT have an up-to-date denominator for
        # alt_btc_log_zscore. Updated unconditionally (open or no position).
        if candle.symbol == "BTCUSDT" and tf == "1h":
            from app.core.features.btc_spread import update_btc_close as _upd_btc
            _upd_btc(candle.close)

        # W4: refresh per-symbol order-flow cache from Binance Futures as a
        # fire-and-forget background task on each 1h candle.  The previous
        # hour's cached value is used for the current observation — at most
        # 1h stale, immaterial for slow order-flow signals.
        # Leading-indicators item 3 (2026-08-06): also persist the same
        # already-fetched values to flow_feature_snapshots for a continuous
        # per-symbol time series (storage only, zero new API cost).
        if tf == "1h":
            import asyncio as _aio
            from app.core.features.flow_features import (
                update_flow_cache_and_persist as _upd_flow,
            )
            _aio.create_task(_upd_flow(candle.symbol))

        key = (candle.symbol, tf)
        if key in self.open_positions:
            await self._maybe_close_position(candle, tf)
        else:
            await self._maybe_open_position(candle, buf, tf)

        # FU-1: heartbeat after every processed candle. Watchdog uses this
        # to detect WS stalls / shadow_worker crashes.
        await record_heartbeat(
            self.session_factory, "shadow_worker",
            status="ok", details={"symbol": candle.symbol, "tf": tf},
        )

    async def _maybe_close_position(
        self, candle: MultiStreamCandle, tf: str,
    ) -> None:
        key = (candle.symbol, tf)
        pos = self.open_positions[key]
        pos.bars_held += 1
        pos.last_check_at = candle.ts

        # Amendment 3 (2026-07-31): append this candle to the position's
        # in-memory bar-history buffer BEFORE running check_exit. The
        # buffer is replayed by the breakeven-variant simulator at
        # close time. Capped to prevent unbounded growth in a
        # pathological hold (should never happen given TIMEOUT_BARS,
        # but the cap is defense in depth). Restart-recovered positions
        # start with an empty buffer — their variants will emit a
        # benign timeout-at-entry row, which is fine (variant lane is
        # analytics, not authoritative).
        try:
            from app.config import get_settings as _get_bev_settings
            from app.shadow.breakeven_variant import BarSnapshot
            _bev_settings = _get_bev_settings()
            if _bev_settings.SHADOW_BREAKEVEN_VARIANTS_ENABLED:
                if len(pos.bar_history) < _bev_settings.SHADOW_BREAKEVEN_BAR_HISTORY_CAP:
                    pos.bar_history.append(BarSnapshot(
                        ts=candle.ts,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                    ))
        except Exception as _bev_buf_err:  # noqa: BLE001 — variant lane is analytics
            log.warning(
                "shadow_worker: bar_history append failed for %s/%s (variant "
                "lane will emit degraded rows): %s",
                candle.symbol, tf, _bev_buf_err,
            )

        decision: ExitDecision | None = check_exit(
            pos,
            bar_high=candle.high,
            bar_low=candle.low,
            bar_close=candle.close,
        )
        if decision is None:
            # T-UI.1: position stays open this candle — emit pnl_tick so
            # the Open Positions card stays fresh between open/close events.
            # Best-effort: failures swallowed, candle processing continues.
            try:
                from app.data.binance_ticker import compute_unrealized_pnl_pct
                pnl_pct = compute_unrealized_pnl_pct(
                    direction=pos.direction.value,
                    entry_price=pos.entry_price,
                    current_price=candle.close,
                )
                if pnl_pct is not None:
                    await shadow_updates.publish_pnl_tick(
                        manager,
                        symbol=candle.symbol,
                        current_price=candle.close,
                        unrealized_pnl_pct=pnl_pct,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "shadow_worker: pnl_tick emit failed for %s/%s: %s",
                    candle.symbol, tf, e,
                )
            return

        inputs_hash = _inputs_hash(candle)
        # PR3: per-TF cooldown from settings. Falls back to the legacy
        # COOLDOWN_MINUTES (30) for any TF not in the dict — preserves
        # pre-PR3 behavior + signals "operator forgot to wire this TF"
        # in the env config.
        from app.config import get_settings as _get_settings_for_cooldown
        _cooldown_table = _get_settings_for_cooldown().SHADOW_COOLDOWN_HOURS
        _cooldown_hours = _cooldown_table.get(tf, COOLDOWN_MINUTES / 60.0)
        cooldown_until = candle.ts + timedelta(hours=_cooldown_hours)

        base_row_hash: str | None = None
        try:
            async with self.session_factory() as session:
                base_row_hash = await persist_closed_trade(
                    session, pos,
                    user_id=self.user_id,
                    exit_price=decision.exit_price,
                    exit_reason=decision.reason,
                    closed_at=candle.ts,
                    bars_held=pos.bars_held,
                    inputs_hash=inputs_hash,
                )
                await delete_open_position(
                    session, user_id=self.user_id,
                    symbol=candle.symbol, timeframe=tf,
                )
                await set_cooldown(
                    session, user_id=self.user_id,
                    symbol=candle.symbol, timeframe=tf,
                    until=cooldown_until,
                )
                await session.commit()
        except Exception as e:
            log.error(
                "persist close failed for %s/%s; suppressing publish: %s",
                candle.symbol, tf, e,
            )
            return

        # Amendment 3 (2026-07-31): breakeven-variant lane. Runs AFTER
        # the base commit succeeds; a separate session so a variant
        # failure cannot roll back the base close. NO cooldown writes,
        # NO touches to open_shadow_positions — confirmations required
        # by the operator ruling that authorized this build. Wrapped
        # best-effort: any exception is logged and swallowed, base
        # publish path continues unchanged.
        if base_row_hash is not None:
            try:
                from app.config import get_settings as _get_bev_persist_settings
                from app.shadow.breakeven_variant import (
                    simulate_variant_exit,
                    variant_name_for,
                )
                _bev_ps = _get_bev_persist_settings()
                _triggers = list(_bev_ps.SHADOW_BREAKEVEN_VARIANT_TRIGGERS_R)
                if (
                    _bev_ps.SHADOW_BREAKEVEN_VARIANTS_ENABLED
                    and _triggers
                ):
                    async with self.session_factory() as _var_session:
                        base_id = await lookup_shadow_trade_id_by_row_hash(
                            _var_session, row_hash=base_row_hash,
                        )
                        if base_id is not None:
                            for _trig in _triggers:
                                _outcome = simulate_variant_exit(
                                    direction=pos.direction.value,
                                    entry_price=pos.entry_price,
                                    initial_stop_loss=pos.stop_loss,
                                    take_profit=pos.take_profit,
                                    timeframe=tf,
                                    trigger_r=float(_trig),
                                    bar_history=list(pos.bar_history),
                                    hold_timeout_bars=pos.hold_timeout_bars,
                                )
                                await insert_shadow_trade_variant(
                                    _var_session,
                                    base_shadow_trade_id=base_id,
                                    variant_name=variant_name_for(float(_trig)),
                                    trigger_r=float(_trig),
                                    armed=_outcome.armed,
                                    exit_price=_outcome.exit_price,
                                    exit_reason=_outcome.exit_reason,
                                    exit_ts=_outcome.exit_ts,
                                    bars_held=_outcome.bars_held,
                                    pnl_pct=_outcome.pnl_pct,
                                )
                            await _var_session.commit()
            except Exception as _var_err:  # noqa: BLE001
                log.warning(
                    "shadow_worker: variant persist failed for %s/%s "
                    "(base close intact; suppressed): %s",
                    candle.symbol, tf, _var_err,
                )

        # Clear bar_history now that the position has closed — memory
        # hygiene (defensive; the position is popped from open_positions
        # a few lines below, but we don't want a lingering reference to
        # retain the buffer if anything holds pos).
        pos.bar_history.clear()

        # FU-33: slippage circuit-breaker. Only fires on SL exits — TP /
        # TIMEOUT are not "slippage" events. Wrapped best-effort so a
        # guard failure cannot suppress the publish path below. The
        # guard short-circuits to halt=False when the flag is OFF, so
        # the only DB I/O on the dormant default is the symbol-halted
        # PK check inside check_slippage (and that only runs on
        # threshold breach, which can't happen flag-OFF).
        if decision.reason is ExitReason.STOP_LOSS:
            from app.config import get_settings as _get_slippage_settings
            from app.shadow.engine import Direction as _Dir
            from app.trading.slippage_guard import check_slippage
            try:
                _entry = pos.entry_price
                if _entry > 0:
                    # Expected = the SL distance the engine sized the
                    # position at (the price would-have-been the trigger
                    # if there were zero slippage).
                    expected_sl_pct = (
                        (pos.stop_loss - _entry) / _entry * 100.0
                        if pos.direction is _Dir.LONG
                        else (_entry - pos.stop_loss) / _entry * 100.0
                    )
                    actual_sl_pct = (
                        (decision.exit_price - _entry) / _entry * 100.0
                        if pos.direction is _Dir.LONG
                        else (_entry - decision.exit_price) / _entry * 100.0
                    )
                    async with self.session_factory() as slip_session:
                        await check_slippage(
                            slip_session, symbol=candle.symbol,
                            expected_sl_pct=expected_sl_pct,
                            actual_sl_pct=actual_sl_pct,
                            settings=_get_slippage_settings(),
                        )
                        await slip_session.commit()
            except Exception as slip_err:  # noqa: BLE001
                log.warning(
                    "shadow_worker: slippage check failed for %s/%s "
                    "(suppressed): %s",
                    candle.symbol, tf, slip_err,
                )

        # Update in-memory caches only after the DB commit succeeds.
        self.open_positions.pop(key, None)
        self.cooldowns[key] = cooldown_until

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
        self, candle: MultiStreamCandle, buf: pd.DataFrame, tf: str,
    ) -> None:
        # FU-33: slippage circuit-breaker. Skip the symbol entirely while
        # an unexpired halt is recorded on `symbol_halt_state`. Always-on
        # at the call-site; check_slippage gates itself on
        # SLIPPAGE_GUARD_ENABLED so a halt row only exists when the
        # operator has flipped the flag and an SL slippage trigger fired.
        # The read is best-effort: a DB blip MUST NOT block legitimate
        # entries — fall through to the normal path on any error.
        from app.trading.slippage_guard import is_symbol_halted
        try:
            async with self.session_factory() as halt_session:
                if await is_symbol_halted(halt_session, symbol=candle.symbol):
                    log.info(
                        "shadow_worker: %s/%s SLIPPAGE_HALT — skipping entry",
                        candle.symbol, tf,
                    )
                    return
        except Exception as e:  # noqa: BLE001
            log.warning(
                "shadow_worker: slippage halt-check failed for %s/%s "
                "(fail-open): %s",
                candle.symbol, tf, e,
            )

        # PR3: PositionGate consumes the per-TF state. open_symbols and
        # cooldowns are filtered to entries matching THIS tf so a 1h BTC
        # cooldown does not block a 15m BTC entry (and vice versa).
        gate = PositionGate(
            open_symbols={s for (s, t) in self.open_positions.keys() if t == tf},
            cooldowns={
                s: until for (s, t), until in self.cooldowns.items() if t == tf
            },
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
                    timeframe=tf,
                )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "pattern_stats lookup failed for %s/%s; running without L2: %s",
                candle.symbol, tf, e,
            )
            stats_lookup = None

        # L8 ghost-candle vote (opt-in, L8_GHOST_SCORING_ENABLED). Unlike
        # live_prediction.py / tab1.py, the shadow worker never computed a
        # ghost candle before — there's no display/persistence use for it
        # here, so the MC-dropout inference (predict_ghost_candle, 32
        # forward passes) is skipped entirely (zero added cost per symbol
        # per candle) unless the flag is on.
        ghost_input: GhostInput | None = None
        if get_settings().L8_GHOST_SCORING_ENABLED and len(buf) >= 256:
            try:
                from app.ml.checkpoints import get_active_model_and_checkpoint
                from app.ml.inference import predict_ghost_candle

                active = get_active_model_and_checkpoint()
                if active is not None:
                    model, _ck = active
                    ghost = predict_ghost_candle(
                        model=model,
                        bars=buf,
                        last_close=float(buf["close"].iloc[-1]),
                    )
                    ghost_input = GhostInput(
                        ghost_close=ghost.close,
                        ghost_uncertainty=ghost.uncertainty,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "predict_ghost_candle failed for %s/%s; L8 abstains: %s",
                    candle.symbol, tf, e,
                )

        # SP-9 Phase E2: build_prediction is async + may consult news_items
        # via the session. Open a short-lived L9 session per candle.
        try:
            async with self.session_factory() as l9_session:
                pred = await build_prediction(
                    symbol=candle.symbol,
                    timeframe=tf,
                    bars=buf,
                    pattern_stats_lookup=stats_lookup,
                    session=l9_session,
                    ghost=ghost_input,
                )
        except Exception as e:
            log.warning(
                "build_prediction failed for %s/%s: %s", candle.symbol, tf, e,
            )
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
            # Visibility into why the gate rejected this bar — score below
            # ±threshold or confidence below 0.50. Without this, prod runs
            # silent for hours and we can't tell if the worker is alive.
            log.info(
                "shadow_worker: %s no-signal score=%.3f conf=%.3f "
                "(needs score>%.2f LONG or score<%.2f SHORT, conf>=%.2f)",
                candle.symbol, pred.final.score, pred.final.confidence,
                self.evaluator.long_threshold,
                self.evaluator.short_threshold,
                self.evaluator.min_confidence,
            )
            return
        log.info(
            "shadow_worker: %s SIGNAL %s score=%.3f conf=%.3f entry=%.6f sl=%.6f tp=%.6f",
            candle.symbol, signal.direction.value, signal.score, signal.confidence,
            signal.entry_price, signal.stop_loss, signal.take_profit,
        )

        # PR-strategy-1: entry-quality gate. Both flags default OFF
        # (MIN_ENTRY_SCORE_LONG=None + DISABLE_SHORT_SIGNALS=False) so this
        # path is a no-op on a fresh deploy. The gate is duck-typed on
        # `.direction` + `.entry_score`; ShadowSignal carries `.score`, so
        # we feed it through a tiny shim that maps `.score → .entry_score`.
        from app.config import get_settings as _get_entry_quality_settings
        _eq_settings = _get_entry_quality_settings()
        _eq_signal = _EntryQualitySignal(
            direction=signal.direction.value, entry_score=signal.score,
        )
        _eq_decision = open_position_gate(_eq_signal, _eq_settings)
        # SHADOW_ALLOW_SHORTS: let shadow trades take short positions while
        # live shorts remain off. Only the short_disabled denial is skipped;
        # score-threshold, regime, and ADX gate reasons still block entry.
        if (
            not _eq_decision.allow
            and _eq_decision.reason == "short_disabled"
            and _eq_settings.SHADOW_ALLOW_SHORTS
        ):
            _eq_decision = AllowDecision(allow=True, reason=None)
        # SHADOW_IGNORE_MIN_SCORE: let shadow trades enter below MIN_ENTRY_SCORE_LONG
        # so the training dataset isn't starved while the live threshold is raised.
        # Uses startswith() because PATTERN_BOOST appends boost detail to the reason.
        # All other gate reasons (regime, ADX, short_disabled) still block entry.
        if (
            not _eq_decision.allow
            and (_eq_decision.reason or "").startswith("below_long_threshold")
            and _eq_settings.SHADOW_IGNORE_MIN_SCORE
        ):
            _eq_decision = AllowDecision(allow=True, reason=None)
        if not _eq_decision.allow:
            log.info(
                "shadow_worker: %s/%s GATE_DENIED %s score=%.3f",
                candle.symbol, tf, _eq_decision.reason, signal.score,
            )
            return

        position = ShadowPosition.from_signal(
            signal, position_size_usdt=SHADOW_POSITION_SIZE_USDT,
        )
        # PR-strategy-1 plumbing fix: propagate PR1 analytics from `pred`
        # onto the in-memory ShadowPosition so persist_closed_trade can
        # forward them into shadow_trades. Restart-survivor positions
        # still get None here (shadow_open_positions doesn't carry these
        # cols — out of scope; see spec §3 KNOWN limitation).
        position.mtf_agreement = getattr(pred, "mtf_agreement", None)
        position.mtf_dominant_tf = getattr(pred, "mtf_dominant_tf", None)
        position.mtf_directions_json = getattr(pred, "mtf_directions_json", None)
        position.mtf_adx_by_tf_json = getattr(pred, "mtf_adx_by_tf_json", None)
        position.p_win = getattr(pred, "p_win", None)
        position.effective_score = getattr(pred, "effective_score", None)
        position.realized_vol_20d = getattr(pred, "realized_vol_20d", None)
        position.funding_directional_adj = getattr(
            pred, "funding_directional_adj", None,
        )
        # PR3: stamp the TF on the position so persistence + the in-memory
        # cache key reflect the actual lane the trade was opened on.
        position.timeframe = tf

        # PR3 Phase 5.5 (G1): when HOLD_TP_SCALING_ENABLED, look up the
        # scaled (timeout_bars, tp_multiplier) for this (tf, mtf_agreement)
        # pair and rewrite take_profit accordingly. Stop-loss is INVARIANT.
        # Recording fields (hold_scaling_factor + hold_timeout_bars) stay
        # NULL when scaling is OFF — bit-identical to pre-G1 trades.
        from app.config import get_settings as _get_pr3_settings
        _pr3_settings = _get_pr3_settings()
        # Pre-bind so the fail-open log call below can reference it even
        # if the getattr() itself raises (theoretical — pred could be a
        # misbehaving proxy). Avoids UnboundLocalError defeating the
        # fail-open contract.
        _mtf_agreement: int | None = None
        if _pr3_settings.HOLD_TP_SCALING_ENABLED:
            from app.shadow.scaling import effective_hold_tp
            try:
                # mtf_agreement may not be on pred (older predictor versions
                # / fail-open paths) — getattr with None default.
                _mtf_agreement = getattr(pred, "mtf_agreement", None)
                scaled_bars, tp_mult = effective_hold_tp(
                    timeframe=tf,
                    mtf_agreement=_mtf_agreement,
                    table=_pr3_settings.HOLD_TP_SCALING_TABLE,
                )
                # Rewrite TP at scaled distance (SL untouched).
                from app.shadow.engine import Direction as _Dir
                _sign = 1.0 if position.direction is _Dir.LONG else -1.0
                _baseline_tp_distance = abs(position.take_profit - position.entry_price)
                position.take_profit = position.entry_price + (
                    _sign * tp_mult * _baseline_tp_distance
                )
                position.hold_scaling_factor = tp_mult
                position.hold_timeout_bars = scaled_bars
            except Exception as _g1_err:  # noqa: BLE001
                # Fail-open per §4.6b: scaling errors must NOT block trade
                # entry. Log + leave position at pre-scaling baseline.
                log.warning(
                    "G1 scaling lookup failed for %s/%s (mtf_agreement=%r): %s — "
                    "opening at baseline",
                    candle.symbol, tf, _mtf_agreement, _g1_err,
                )

        # Build the 9-float layer_scores array (signed_strength * confidence,
        # per the aggregator's contribution formula) in deterministic L1..L9
        # order. None layers → 0.0. Used by the obs-snapshot for the brain's
        # 58-float observation reconstruction.
        # Keys in pred.layer_scores are stringified ints ("1".."9") — see
        # core/predictor.py:471 — not raw ints. The int-keyed lookup was a
        # silent always-None bug masked by the None-guard below; mypy caught
        # it 2026-05-15 once the dict type tightened.
        layer_scores_array: list[float] = []
        for i in range(1, 10):
            ls = pred.layer_scores.get(str(i))
            if ls is None:
                layer_scores_array.append(0.0)
                continue
            try:
                dir_val = getattr(ls, "direction", "NEUTRAL")
                if hasattr(dir_val, "value"):
                    dir_val = dir_val.value
                sign = 1.0 if dir_val == "LONG" else -1.0 if dir_val == "SHORT" else 0.0
                strength = float(getattr(ls, "strength", 0.0) or 0.0)
                confidence = float(getattr(ls, "confidence", 0.0) or 0.0)
                layer_scores_array.append(sign * strength * confidence)
            except Exception:  # noqa: BLE001 — obs-builder is best-effort
                layer_scores_array.append(0.0)

        try:
            async with self.session_factory() as session:
                await persist_open_position(
                    session, position, user_id=self.user_id,
                )
                # Capture the full RL obs components for offline PPO training.
                # Best-effort — failures here don't block the position open;
                # missing obs rows only degrade future training-data quality.
                try:
                    components = await build_obs_components(
                        session,
                        symbol=candle.symbol,
                        captured_at=candle.ts,
                        atr=atr_value,
                        last_close=candle.close,
                        layer_scores_array=layer_scores_array,
                        bars=buf,
                    )
                    await persist_observation(
                        session,
                        signal_id=signal.signal_id,
                        user_id=self.user_id,
                        symbol=candle.symbol,
                        opened_at=candle.ts,
                        components=components,
                    )
                except Exception as obs_err:  # noqa: BLE001
                    log.warning(
                        "shadow obs capture failed for %s (signal_id=%s): %s",
                        candle.symbol, signal.signal_id, obs_err,
                    )
                await session.commit()
        except Exception as e:
            log.error("persist open failed for %s; suppressing publish: %s",
                      candle.symbol, e)
            return

        self.open_positions[(candle.symbol, tf)] = position

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


def _klines_to_dataframe(klines: list[list[Any]]) -> pd.DataFrame:
    """Convert raw Binance kline rows (the MTF cache format) to the
    OHLCV-indexed DataFrame the shadow worker's bars buffer expects.

    Binance kline schema per /api/v3/klines:
      [0] open_time (ms), [1] open, [2] high, [3] low, [4] close,
      [5] volume, [6] close_time, ...

    Used by `setup()` when it reuses PR1's `_KLINE_CACHE` instead of
    REST-fetching via `BinanceClient.fetch_klines` (which returns
    `Candle` objects via `c.__dict__`).
    """
    if not klines:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
        )
    rows = [
        {
            "ts": pd.to_datetime(int(k[0]), unit="ms", utc=True),
            "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in klines
    ]
    df = pd.DataFrame(rows)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]]


def _candles_to_raw_klines(
    candles: list[Any],
) -> list[list[Any]]:
    """Convert the worker's `Candle` list to the raw Binance kline format
    the MTF cache expects. Best-effort populate path — Candle objects
    have `ts`, `open`, `high`, `low`, `close`, `volume` attributes;
    Binance klines are positional 12-element lists.

    Only positions 0-5 are used by `_vote_for_tf`; trailing fields can
    be empty placeholders since neither the MTF compute nor the shadow
    seed reads them.
    """
    out: list[list[Any]] = []
    for c in candles:
        try:
            ts_dt = c.ts
            # pd.Timestamp / datetime → ms epoch
            ts_ms = int(ts_dt.timestamp() * 1000)
            out.append([
                ts_ms,
                str(c.open), str(c.high), str(c.low), str(c.close),
                str(c.volume),
                ts_ms + 3_599_999,  # close_time placeholder
                "0", 0, "0", "0", "0",  # quote_vol, trades, taker buy, ignore
            ])
        except Exception:  # noqa: BLE001 — best-effort; skip malformed
            continue
    return out


async def _load_open_position_symbols(session: AsyncSession) -> set[str]:
    """Symbols with at least one open position, regardless of universe / blacklist.

    PR-CLEANUP-BATCH-1 §I: prevents universe drift from orphaning positions
    (TRUMPUSDT-class issue from PR-DIAG-AUDIT-BUNDLE — a symbol dropped from
    the top-30 universe still has an open position, but the worker stops
    subscribing to it, leaving ``last_check_at`` stale and the exit monitor
    blind to the symbol). Union with the top-30 universe at boot fixes this.
    """
    result = await session.execute(sa.text(
        "SELECT DISTINCT symbol FROM shadow_open_positions"
    ))
    return {r.symbol for r in result}


def _compute_subscription_set(
    top_n: Iterable[str], open_position_symbols: Iterable[str],
) -> list[str]:
    """Deduped + sorted union — stable ordering for log readability."""
    return sorted(set(top_n) | set(open_position_symbols))


async def _build_default_worker() -> ShadowWorker:
    """Build a worker using the production session factory + settings.

    PR3: reads ``SHADOW_TIMEFRAMES`` and ``SHADOW_NARROW_UNIVERSE`` from
    Settings. Spawns one ``MultiStreamReader`` per TF and registers them
    via the worker's ``readers`` dict. Pre-PR3 callers that imported the
    legacy single-reader build path can continue to construct the worker
    directly with ``reader=...`` — this helper is the production wiring
    only.

    PR-CLEANUP-BATCH-1 §I: the subscription set is
    ``top_30 ∪ open_position_symbols`` (deduped, sorted). Open positions
    on symbols that have since dropped from the universe still receive
    WS ticks and can be exited cleanly.
    """
    from app.config import get_settings
    settings = get_settings()
    timeframes: list[str] = list(settings.SHADOW_TIMEFRAMES) or [SHADOW_TIMEFRAME]
    narrow: list[str] = list(settings.SHADOW_NARROW_UNIVERSE)

    factory = get_session_factory()
    async with factory() as session:
        top_n = await load_shadow_universe(session, narrow=narrow)
        # PR-CLEANUP-BATCH-1 §I: union with open positions.
        open_position_symbols = await _load_open_position_symbols(session)

    symbols = _compute_subscription_set(top_n, open_position_symbols)
    extra = sorted(set(open_position_symbols) - set(top_n))
    if extra:
        log.info(
            "shadow_worker: subscription union added %d open-position "
            "symbol(s) outside top-N: %s",
            len(extra), extra,
        )

    readers: dict[str, _StreamReader] = {
        tf: MultiStreamReader(symbols=symbols, timeframe=tf) for tf in timeframes
    }
    log.info(
        "shadow_worker: build_default → %d symbols × %d TF(s) %s",
        len(symbols), len(timeframes), timeframes,
    )
    return ShadowWorker(
        symbols=symbols,
        session_factory=factory,
        # `reader` is the primary (first TF) — backward-compat with the
        # legacy single-reader code path. `readers` carries the full set.
        reader=readers[timeframes[0]],
        timeframes=timeframes,
        readers=readers,
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


