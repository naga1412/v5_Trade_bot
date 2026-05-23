"""SP-8 Phase J — execution dispatcher.

When the bot generates a signal, this module decides what to do based
on the user's ``trading_mode``:

  manual              -> emit only; user clicks Place Trade in Tab 1
  telegram-approve    -> send Telegram message, await callback
  fully-auto          -> place order immediately via BinanceLiveClient

Spec sec 3 + sec 13. The actual signal generation is upstream
(``app/core/predictor.py``); this dispatcher just routes the signal.

Inputs (per call):
  * SignalProposal (symbol, direction, entry, SL, TP, etc.)
  * UserContext (id, mode, vault-decrypted Binance keys, settings)

Outputs:
  * DispatchResult — what happened: emitted / sent_telegram / placed
    / blocked-by-killswitch / blocked-by-cooldown / error.

Pre-conditions checked here:
  * Kill switches not tripped (sec 11)
  * Per-asset cooldown elapsed (sec 2.6)
  * Max concurrent positions not exceeded (sec 2.5)
  * Funding rate guard (sec 11.6)

Side effects:
  * Telegram-approve mode: writes a telegram_signals row
  * Fully-auto mode: places order, writes a live_trades row
  * Both: ALL kill switches + auto-demote rules continue to apply
    via the kill_switches polling worker (separate task)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.audit import insert_with_chain
from app.db.payload_builders import build_live_trade_payload
from app.exchanges.binance_filters import (
    get_symbol_filters,
    quantize_qty,
)
from app.exchanges.binance_live import (
    BinanceLiveClient,
    BinanceLiveError,
    OrderRejected,
)
from app.telegram.signals import (
    SignalCandidate,
    build_signal_payload,
    render_message,
    serialise_payload,
)
from app.telegram.trade_signals import (
    TelegramTradeConfig,
    send_trade_signal_message,
)
from app.trading.kill_switches import (
    DEFAULTS as KILL_DEFAULTS,
    SwitchConfig,
    evaluate_funding_rate,
)
from app.trading.leverage import recommended_leverage
from app.trading.modes import Mode, get_mode
from app.trading.position_sizing import (
    FixedSizingConfig,
    SizingMode,
    compute_position_margin,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignalProposal:
    """Raw signal the upstream scoring engine produced."""

    symbol: str               # canonical e.g. BTC/USDT
    timeframe: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    confidence_pct: float     # 0-100
    layer_summary: dict[str, dict]
    inputs_hash: str
    funding_rate_daily: float = 0.0
    chart_base_url: str = ""
    # PR2: MTF fields threaded from LivePredictionOut. None when PR1 MTF
    # compute returned None (cold cache, fetch failure) or when the caller
    # pre-dates the threading (recording-only fallback / older test fixture).
    # `mtf_directions` is the PARSED dict; the JSON string form lives only
    # at the DB layer (live_trades.mtf_directions_json).
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions: dict[str, int] | None = None
    # PR-strategy-1: aggregator's final signed score (`pred.final.score`)
    # threaded through `proposal_from_prediction`. The dispatcher's
    # entry-quality gate reads this directly. None when the caller
    # constructs a SignalProposal without a pred (`admin_test_trade.py`
    # manual entry, ad-hoc operator test). The gate treats None as "no
    # score available" → allow (operator-driven manual entry should not
    # be blocked by a flag the operator hasn't opted into).
    entry_score: float | None = None


@dataclass(frozen=True)
class UserContext:
    """Resolved per-user state for dispatch."""

    user_id: int
    mode: Mode
    binance_api_key: str
    binance_api_secret: str
    use_testnet: bool
    portfolio_value_usdt: float
    successful_trades: int
    sizing_mode: SizingMode
    fixed_size_usdt: float | None  # None when sizing_mode == "percent"
    max_leverage_cap: int
    max_concurrent_positions: int
    open_positions_count: int


DispatchOutcome = Literal[
    "emitted",                # manual mode — no action
    "sent_telegram",          # message sent, awaiting callback
    "placed",                 # fully-auto: order placed
    "blocked_killswitch",
    "blocked_cooldown",
    "blocked_max_positions",
    "blocked_funding",
    "error",
    # PR2 additions — MTF gate + SHORT safety branches
    "blocked_mtf_low_agreement",
    "blocked_mtf_higher_tf_veto",
    "blocked_short_high_borrow",
    # PR10 additions — symbol allowlist
    "blocked_stablecoin",
    "blocked_low_sharpe",
    # PR-strategy-1 — entry-quality gate
    "blocked_entry_quality",
]


@dataclass(frozen=True)
class DispatchResult:
    outcome: DispatchOutcome
    detail: str
    signal_id: str | None = None        # set for sent_telegram / placed
    binance_order_id: str | None = None  # set for placed
    leverage_chosen: int | None = None


def _compute_sl_distance_pct(
    entry: float, sl: float, direction: str,
) -> float:
    if entry <= 0:
        return 0.0
    if direction == "LONG":
        return max(0.0, (entry - sl) / entry)
    return max(0.0, (sl - entry) / entry)


# ---------------------------------------------------------------------------
# PR2 — MTF gate + higher-TF veto
# ---------------------------------------------------------------------------


def _apply_mtf_gate(
    proposal: SignalProposal,
    settings: Settings,
) -> DispatchResult | None:
    """Decide whether MTF state should short-circuit dispatch.

    Returns a `DispatchResult(outcome="blocked_mtf_*")` to abort dispatch
    or `None` to fall through.

    Fail-open contract (spec §4.2 + R6):
      - `mtf_agreement is None` → PASS. PR1 MTF compute returns None on
        cold cache / fetch failure; we must never poison dispatch on
        missing signal data.
      - `MTF_MIN_AGREEMENT_1H=0` → PASS for every agreement value. This is
        the single-env-var rollback path (spec §8).
      - `mtf_directions is None` → veto cannot evaluate → PASS.

    The function is pure: no mutation of `proposal`, no DB I/O, no module-
    level state reads. `settings` MUST be passed in (not read via
    `get_settings()`) so env-var overrides take effect at request time.
    """
    if (
        proposal.mtf_agreement is not None
        and proposal.mtf_agreement < settings.MTF_MIN_AGREEMENT_1H
    ):
        return DispatchResult(
            outcome="blocked_mtf_low_agreement",
            detail=(
                f"mtf_agreement={proposal.mtf_agreement} < "
                f"MTF_MIN_AGREEMENT_1H={settings.MTF_MIN_AGREEMENT_1H}"
            ),
        )

    if settings.MTF_HIGHER_TF_VETO and proposal.mtf_directions is not None:
        d_1d = proposal.mtf_directions.get("1d", 0)
        d_1w = proposal.mtf_directions.get("1w", 0)
        if proposal.direction == "LONG" and d_1d < 0 and d_1w < 0:
            return DispatchResult(
                outcome="blocked_mtf_higher_tf_veto",
                detail="1d and 1w both opposite LONG (negative)",
            )
        if proposal.direction == "SHORT" and d_1d > 0 and d_1w > 0:
            return DispatchResult(
                outcome="blocked_mtf_higher_tf_veto",
                detail="1d and 1w both opposite SHORT (positive)",
            )

    return None


# ---------------------------------------------------------------------------
# PR2 — SHORT-side safety: high-borrow veto
# ---------------------------------------------------------------------------


# Spec §R7: treat borrow data older than this as missing → fail-open.
_BORROW_STALENESS_BUDGET = timedelta(hours=6)


async def _lookup_borrow_apr(
    symbol: str, session: AsyncSession,
) -> float | None:
    """Latest borrow APR % for ``symbol`` from intermarket_snapshots.

    Returns None when:
      - No `intermarket_snapshots.borrow_rate_pct` row exists for the
        symbol (current prod state — the column isn't wired yet; the
        BinanceFuturesIntermarketAdapter doesn't populate it).
      - The latest row is older than the spec §R7 staleness budget.
      - The query itself raises (defensive: never let dispatch fail
        because the borrow lookup blew up — the flag defaults OFF so
        production behavior is unchanged).

    Fail-open contract: callers treat None as "no veto".

    NOTE: as of PR2 the intermarket_snapshots schema lacks `borrow_rate_pct`
    (migration 0014 added funding_rate + open_interest only). This helper
    consequently returns None in prod. The SHORT_VETO_HIGH_BORROW flag
    therefore stays dormant by construction until a future PR wires the
    real borrow-rate source. Tests inject borrow values via patch.
    """
    try:
        row = (await session.execute(
            sa.text(
                "SELECT borrow_rate_pct, captured_at "
                "FROM intermarket_snapshots "
                "WHERE symbol = :sym "
                "ORDER BY captured_at DESC LIMIT 1"
            ),
            {"sym": symbol},
        )).fetchone()
    except Exception as exc:  # noqa: BLE001 — fail-open per §R7
        log.warning(
            "_lookup_borrow_apr(%s) raised — failing open: %s", symbol, exc,
        )
        return None
    if row is None or row.borrow_rate_pct is None:
        return None
    captured_at = row.captured_at
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - captured_at
    if age > _BORROW_STALENESS_BUDGET:
        log.info(
            "borrow data stale for %s (age=%.1fh) — failing open",
            symbol, age.total_seconds() / 3600,
        )
        return None
    return float(row.borrow_rate_pct)


async def _apply_short_safety_gates(
    proposal: SignalProposal,
    settings: Settings,
    *,
    session: AsyncSession,
) -> DispatchResult | None:
    """SHORT-side blocking gates. Returns a DispatchResult to short-circuit
    or None to fall through.

    Spec §4.2: only runs when proposal.direction == "SHORT". LONG signals
    return immediately — borrow lookup is not performed.

    Current gate: SHORT_VETO_HIGH_BORROW. SL tightening lives in
    `_maybe_tighten_short_sl` (modification, not a block). Hold-time
    halving lives in the exit-timeout path (Phase 5).
    """
    if proposal.direction != "SHORT":
        return None

    if settings.SHORT_VETO_HIGH_BORROW:
        borrow_apr = await _lookup_borrow_apr(proposal.symbol, session)
        if (
            borrow_apr is not None
            and borrow_apr > settings.SHORT_VETO_BORROW_APR_PCT
        ):
            return DispatchResult(
                outcome="blocked_short_high_borrow",
                detail=(
                    f"borrow_apr={borrow_apr:.2f}% > threshold "
                    f"{settings.SHORT_VETO_BORROW_APR_PCT:.2f}%"
                ),
            )

    return None


# ---------------------------------------------------------------------------
# PR2 — SHORT-side safety: SL tightening (modifies proposal, never blocks)
# ---------------------------------------------------------------------------


def _maybe_tighten_short_sl(
    proposal: SignalProposal,
    settings: Settings,
) -> SignalProposal:
    """Tighten a SHORT proposal's stop-loss when MTF agreement is low.

    Spec §4.2: when SHORT_TIGHTEN_SL_LOW_MTF is ON, the proposal is SHORT,
    and mtf_agreement < cutoff, reduce the distance between entry and SL
    by SHORT_TIGHTEN_SL_PCT. Otherwise returns the input proposal unchanged
    (same object identity — caller relies on `is` for the no-op branch in
    micro-perf benchmarks).

    No DispatchOutcome change — this is a modification, not a block.

    SHORT trade geometry: SL sits ABOVE entry. "Tighten" means reduce the
    upward distance so the stop fires earlier. e.g. entry=100, SL=110
    (distance 10), 20% tightening → new distance 8 → new SL=108.
    """
    if (
        proposal.direction != "SHORT"
        or not settings.SHORT_TIGHTEN_SL_LOW_MTF
        or proposal.mtf_agreement is None
        or proposal.mtf_agreement >= settings.SHORT_TIGHTEN_SL_MTF_CUTOFF
    ):
        return proposal

    sl_distance = proposal.stop_loss_price - proposal.entry_price
    new_distance = sl_distance * (1.0 - settings.SHORT_TIGHTEN_SL_PCT)
    new_sl = proposal.entry_price + new_distance
    return dataclasses.replace(proposal, stop_loss_price=new_sl)


def _compute_rr_ratio(entry: float, sl: float, tp: float) -> float:
    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    if sl_dist <= 0:
        return 0.0
    return tp_dist / sl_dist


async def _check_funding_block(
    proposal: SignalProposal,
) -> tuple[bool, str | None]:
    """Spec sec 11.6: funding rate guard. Returns (blocked, reason)."""
    cfg = SwitchConfig(
        name="funding_rate_guard", enabled=True,
        threshold_value=KILL_DEFAULTS["funding_rate_guard"],
    )
    decision = evaluate_funding_rate(
        daily_funding_rate=proposal.funding_rate_daily,
        position_direction=proposal.direction,
        cfg=cfg,
    )
    if decision.tripped:
        return True, decision.reason
    return False, None


async def _send_telegram_signal(
    session: AsyncSession,
    *,
    user: UserContext,
    proposal: SignalProposal,
    leverage: int,
    margin_usdt: float,
    now: datetime,
) -> str:
    """Build + persist a telegram_signals row, send the outbound Telegram
    message, and return signal_id.

    Originally this only wrote the DB row — the polling worker was
    supposed to scan for unsent rows and POST them. That polling-side
    pickup was never actually wired (the poller only handles inbound
    callbacks, not outbound new-signal sends), so signals piled up in
    the DB and operators never received notifications. Now we INSERT
    the row + immediately POST via send_trade_signal_message, all in
    one transaction.

    The DB row stays as the source of truth: send_trade_signal_message
    updates it with the Telegram message_id on success so the
    edit-on-callback path (poller) can locate the right message later.
    If the outbound POST fails (no creds, network blip, Telegram API
    error), the row is still in the DB and a retry from a future
    healer or manual ops command can flush it without losing context.
    """
    sig_id = "sig_" + secrets.token_hex(8)
    candidate = SignalCandidate(
        signal_id=sig_id,
        symbol=proposal.symbol,
        timeframe=proposal.timeframe,
        direction=proposal.direction,
        entry_price=proposal.entry_price,
        stop_loss_price=proposal.stop_loss_price,
        take_profit_price=proposal.take_profit_price,
        confidence_pct=proposal.confidence_pct,
        layer_summary=proposal.layer_summary,
        margin_usdt=margin_usdt,
        funding_rate_daily=proposal.funding_rate_daily,
        chart_url=(
            f"{proposal.chart_base_url}/tab1/"
            f"{proposal.symbol.replace('/', '-')}/"
            f"{proposal.timeframe}?signal={sig_id}"
        ),
        sl_distance_pct=_compute_sl_distance_pct(
            proposal.entry_price, proposal.stop_loss_price, proposal.direction,
        ),
        rr_ratio=_compute_rr_ratio(
            proposal.entry_price, proposal.stop_loss_price,
            proposal.take_profit_price,
        ),
        # PR2: persist MTF state in telegram_signals.payload so the
        # approve-time path can populate live_trades.mtf_* (§4.4 Phase 7).
        # Gate already fired upstream; these fields just preserve state.
        mtf_agreement=proposal.mtf_agreement,
        mtf_dominant_tf=proposal.mtf_dominant_tf,
        mtf_directions=proposal.mtf_directions,
    )
    payload = build_signal_payload(
        candidate, rendered_at=now, initial_leverage=leverage,
    )
    rendered = render_message(candidate, leverage=leverage, now=now)
    payload["rendered_body"] = rendered.body
    payload["inline_keyboard"] = rendered.inline_keyboard

    await session.execute(
        sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload) "
            "VALUES (:id, :u, :s, :d, :ts, :p)"
        ),
        {
            "id": sig_id, "u": user.user_id, "s": proposal.symbol,
            "d": proposal.direction, "ts": now,
            "p": serialise_payload(payload),
        },
    )

    # Outbound POST to Telegram. Reads bot_token + chat_id from env
    # (same as the polling worker uses for inbound). Best-effort here —
    # if the message fails to send (creds missing, Telegram outage,
    # network), we log and return the signal_id anyway so the dispatch
    # appears as outcome=sent_telegram. The DB row is the source of
    # truth so manual retry is always possible.
    import os
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if bot_token and chat_id:
        try:
            msg_id = await send_trade_signal_message(
                session, signal_id=sig_id,
                config=TelegramTradeConfig(
                    bot_token=bot_token, chat_id=chat_id,
                ),
            )
            if msg_id is None:
                log.error(
                    "_send_telegram_signal: signal %s row written but "
                    "outbound POST failed; check Telegram bot creds + "
                    "network reachability to api.telegram.org",
                    sig_id,
                )
        except Exception as e:  # noqa: BLE001
            log.exception(
                "_send_telegram_signal: outbound POST raised for %s: %s",
                sig_id, e,
            )
    else:
        log.warning(
            "_send_telegram_signal: signal %s queued in DB but "
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset — operator will "
            "NOT see the message until those are configured",
            sig_id,
        )
    return sig_id


def _binance_native(symbol: str) -> str:
    """BTC/USDT -> BTCUSDT for the Binance API call."""
    return symbol.replace("/", "")


async def _place_live_order(
    session: AsyncSession,
    *,
    user: UserContext,
    proposal: SignalProposal,
    leverage: int,
    margin_usdt: float,
    now: datetime,
) -> tuple[str, str]:
    """Place a real Binance order + write the live_trades row.

    Returns (binance_order_id, our_signal_id).
    """
    sig_id = "sig_" + secrets.token_hex(8)
    side: Literal["BUY", "SELL"] = (
        "BUY" if proposal.direction == "LONG" else "SELL"
    )
    raw_qty = (margin_usdt * leverage) / proposal.entry_price

    # Quantize qty to the symbol's LOT_SIZE.stepSize. Without this,
    # Binance rejects with -1111 "Precision is over the maximum" for
    # any qty that isn't a clean multiple of the step (e.g. 0.000637
    # BTC at $79k mark vs BTCUSDT's stepSize=0.001). Caught
    # 2026-05-16 via test-trade-fully-auto-round-trip probe.
    binance_native_sym = _binance_native(proposal.symbol)
    filters = await get_symbol_filters(
        binance_native_sym, use_testnet=user.use_testnet,
    )
    if filters is None:
        raise OrderRejected(
            f"_place_live_order: no Binance filters for {binance_native_sym} "
            f"(symbol delisted or exchangeInfo fetch failed)",
        )
    qty = quantize_qty(raw_qty, filters.step_size)
    if qty < filters.min_qty:
        raise OrderRejected(
            f"_place_live_order: quantized qty {qty} below symbol min "
            f"{filters.min_qty} for {binance_native_sym}. Increase "
            f"margin (currently {margin_usdt}) or leverage (currently "
            f"{leverage}x) so margin × leverage / entry_price ≥ min_qty.",
        )
    notional = qty * proposal.entry_price
    if filters.min_notional > 0 and notional < filters.min_notional:
        raise OrderRejected(
            f"_place_live_order: notional {notional:.2f} below symbol min "
            f"{filters.min_notional:.2f} for {binance_native_sym}",
        )

    client = BinanceLiveClient(
        api_key=user.binance_api_key,
        api_secret=user.binance_api_secret,
        use_testnet=user.use_testnet,
    )
    try:
        order = await client.place_order(
            symbol=binance_native_sym,
            side=side, quantity=qty, leverage=leverage,
            order_type="MARKET",
        )
    finally:
        await client.aclose()

    payload = build_live_trade_payload(
        user_id=user.user_id,
        symbol=proposal.symbol,
        direction=proposal.direction,
        margin_usdt=margin_usdt,
        leverage=leverage,
        entry_price=float(order.avg_fill_price or proposal.entry_price),
        stop_loss=proposal.stop_loss_price,
        take_profit=proposal.take_profit_price,
        binance_order_id=order.binance_order_id,
        opened_at=now,
        mode_at_open=user.mode,
        approved_via="auto",
        reasoning_json=json.dumps({
            "confidence_pct": proposal.confidence_pct,
            "layer_summary": proposal.layer_summary,
            "signal_id": sig_id,
        }),
        inputs_hash=proposal.inputs_hash,
        # PR2 §4.4: persist MTF state on live_trades so post-trade
        # analytics can correlate gate state to outcome. None on PR1
        # fallback paths.
        mtf_agreement=proposal.mtf_agreement,
        mtf_dominant_tf=proposal.mtf_dominant_tf,
        mtf_directions=proposal.mtf_directions,
    )
    await insert_with_chain(session, "live_trades", payload)
    return order.binance_order_id, sig_id


async def dispatch(
    session: AsyncSession,
    *,
    proposal: SignalProposal,
    user: UserContext,
    now: datetime | None = None,
) -> DispatchResult:
    """Top-level entry point. Calls into the right sub-path per mode.

    Caller commits the session.
    """
    n = now or datetime.now(timezone.utc)

    # Re-resolve mode from DB to avoid acting on stale UserContext.mode.
    current_mode = await get_mode(session, user.user_id)

    if current_mode == "manual":
        return DispatchResult(
            outcome="emitted",
            detail="manual mode — signal exposed via Tab 1, no auto-action",
        )

    # ---- Pre-conditions ----

    # ---- PR10 symbol allowlist gate (cheapest check, run first) --------
    # Default-OFF in prod via SYMBOL_ALLOWLIST_ENABLED=False — when
    # disabled, _apply_symbol_allowlist_gate short-circuits to None
    # without touching the DB.
    from app.config import get_settings as _get_pr10_settings
    from app.trading.execution.symbol_allowlist_gate import _apply_symbol_allowlist_gate
    pr10_settings = _get_pr10_settings()
    allowlist_block = await _apply_symbol_allowlist_gate(
        proposal=proposal, user_id=user.user_id,
        session=session, settings=pr10_settings,
        now_fn=lambda: n,
    )
    if allowlist_block is not None:
        return allowlist_block
    # ---- end PR10 ------------------------------------------------------

    # ---- PR-strategy-1 entry-quality gate ------------------------------
    # Both flags default OFF (MIN_ENTRY_SCORE_LONG=None, DISABLE_SHORT_
    # SIGNALS=False) — when off, `open_position_gate` short-circuits to
    # allow without touching the DB. The gate reads `proposal.entry_score`
    # (threaded from `pred.final.score` via `proposal_from_prediction`).
    from app.config import get_settings as _get_entry_quality_settings
    from app.core.gates.entry_quality import open_position_gate
    _eq_decision = open_position_gate(
        proposal, _get_entry_quality_settings(),
    )
    if not _eq_decision.allow:
        return DispatchResult(
            outcome="blocked_entry_quality",
            detail=f"entry_quality_gate: {_eq_decision.reason}",
        )
    # ---- end PR-strategy-1 entry-quality gate --------------------------

    # Funding-rate guard
    blocked, reason = await _check_funding_block(proposal)
    if blocked:
        return DispatchResult(
            outcome="blocked_funding",
            detail=reason or "funding-rate guard tripped",
        )

    # ---- PR8 cooldown gate (cheapest DB check, run early) ---------------
    # Reads live_cooldowns for (user_id, symbol). Default-OFF in prod via
    # LIVE_COOLDOWN_ENABLED=False — when disabled, _apply_cooldown_gate
    # short-circuits to None without touching the DB.
    from app.config import get_settings as _get_pr8_settings
    from app.trading.execution.cooldown_gate import _apply_cooldown_gate
    pr8_settings = _get_pr8_settings()
    cooldown_result = await _apply_cooldown_gate(
        proposal=proposal, user_id=user.user_id,
        session=session, settings=pr8_settings,
        now_fn=lambda: n,
    )
    if cooldown_result is not None:
        return cooldown_result
    # ---- end PR8 cooldown gate ------------------------------------------

    # ---- PR2 gates (MTF + SHORT safety) ---------------------------------
    # Order: MTF gate first (cheapest — no DB I/O), then SHORT safety
    # (one DB read), then SL tightening (modifies proposal). After this
    # block, `proposal` may be a NEW SignalProposal instance with a
    # tightened stop-loss — the rest of dispatch() must use the returned
    # value, not the original.
    #
    # Spec §8 rollback path (`MTF_MIN_AGREEMENT_1H=0`): `get_settings()`
    # is @lru_cache-wrapped, so the rollback requires a process restart
    # OR an explicit `get_settings.cache_clear()` call from an
    # operational endpoint. This matches every other env-var-driven
    # flag in the codebase (BINANCE_USE_TESTNET, AUTONOMOUS_TRADING_*,
    # etc.) — env mutations always require restart. Document'd in PR2
    # rollback runbook (KNOWN_ISSUES / docs/ARCHITECTURE.md).
    from app.config import get_settings as _get_pr2_settings
    pr2_settings = _get_pr2_settings()
    gate_result = _apply_mtf_gate(proposal, pr2_settings)
    if gate_result is not None:
        return gate_result
    gate_result = await _apply_short_safety_gates(
        proposal, pr2_settings, session=session,
    )
    if gate_result is not None:
        return gate_result
    proposal = _maybe_tighten_short_sl(proposal, pr2_settings)
    # ---- end PR2 gates --------------------------------------------------

    # Max concurrent
    if user.open_positions_count >= user.max_concurrent_positions:
        return DispatchResult(
            outcome="blocked_max_positions",
            detail=(
                f"{user.open_positions_count}/{user.max_concurrent_positions} "
                "positions open; refuse new entry"
            ),
        )

    # ---- Compute leverage + sizing ----

    sl_pct = _compute_sl_distance_pct(
        proposal.entry_price, proposal.stop_loss_price, proposal.direction,
    )
    leverage = recommended_leverage(
        margin_usdt=0.0,  # not used in current formula
        sl_distance_pct=sl_pct,
        hard_cap=user.max_leverage_cap,
    )

    # ---- PR9 dynamic sizing (default-OFF; falls back to legacy on None) -
    # When DYNAMIC_SIZING_ENABLED=True, compute_dynamic_size returns the
    # Kelly-fractional × balance-tier × hard-cap margin. Returns None on
    # disabled OR on any compute error (fail-open contract) — caller
    # falls through to the legacy fixed/percent path below.
    from app.config import get_settings as _get_pr9_settings
    from app.trading.dynamic_sizing import compute_dynamic_size
    pr9_settings = _get_pr9_settings()
    dynamic_margin = compute_dynamic_size(
        balance_usdt=user.portfolio_value_usdt,
        confidence_pct=proposal.confidence_pct,
        settings=pr9_settings,
    )

    if dynamic_margin is not None:
        margin = dynamic_margin
    elif user.sizing_mode == "fixed":
        if user.fixed_size_usdt is None:
            return DispatchResult(
                outcome="error",
                detail="fixed sizing mode but fixed_size_usdt is None",
            )
        margin = compute_position_margin(
            mode="fixed",
            fixed_config=FixedSizingConfig(amount_usdt=user.fixed_size_usdt),
        )
    else:
        margin = compute_position_margin(
            mode="percent",
            portfolio_value_usdt=user.portfolio_value_usdt,
            successful_trades=user.successful_trades,
        )

    # ---- Mode-specific path ----

    try:
        if current_mode == "telegram-approve":
            # ---- PR-HYBRID-CONFIDENCE-ROUTING (2026-05-23) --------------
            # Inside telegram-approve mode, when HYBRID_AUTO_SCORE_THRESHOLD
            # is set AND the signal's |entry_score| meets the threshold,
            # route directly to _place_live_order (skip Telegram approval).
            # Below-threshold or no-score signals fall through to the
            # existing telegram-approve handshake. Default None keeps
            # this dormant; flip on by setting the env var + restart.
            from app.config import get_settings as _get_hybrid_settings
            _hybrid_threshold = _get_hybrid_settings().HYBRID_AUTO_SCORE_THRESHOLD
            if (
                _hybrid_threshold is not None
                and proposal.entry_score is not None
                and abs(proposal.entry_score) >= _hybrid_threshold
            ):
                log.info(
                    "hybrid_routing: user=%d %s/%s score=%.3f >= "
                    "HYBRID_AUTO_SCORE_THRESHOLD=%.3f -> _place_live_order "
                    "(skip Telegram approval)",
                    user.user_id, proposal.symbol, proposal.direction,
                    abs(proposal.entry_score), _hybrid_threshold,
                )
                order_id, sig_id = await _place_live_order(
                    session, user=user, proposal=proposal,
                    leverage=leverage, margin_usdt=margin, now=n,
                )
                return DispatchResult(
                    outcome="placed_hybrid",
                    detail=(
                        f"hybrid auto-execute: "
                        f"|score|={abs(proposal.entry_score):.3f} "
                        f">= {_hybrid_threshold:.3f}; "
                        f"placed {proposal.direction} {proposal.symbol} "
                        f"qty={(margin*leverage)/proposal.entry_price:.6f} @ "
                        f"~${proposal.entry_price:.2f} lev={leverage}× "
                        f"binance_order={order_id}"
                    ),
                    signal_id=sig_id, binance_order_id=order_id,
                    leverage_chosen=leverage,
                )
            # ---- end PR-HYBRID-CONFIDENCE-ROUTING -----------------------

            sig_id = await _send_telegram_signal(
                session, user=user, proposal=proposal,
                leverage=leverage, margin_usdt=margin, now=n,
            )
            return DispatchResult(
                outcome="sent_telegram",
                detail=f"Telegram message queued ({sig_id})",
                signal_id=sig_id, leverage_chosen=leverage,
            )

        # fully-auto
        order_id, sig_id = await _place_live_order(
            session, user=user, proposal=proposal,
            leverage=leverage, margin_usdt=margin, now=n,
        )
        return DispatchResult(
            outcome="placed",
            detail=(
                f"placed {proposal.direction} {proposal.symbol} "
                f"qty={(margin*leverage)/proposal.entry_price:.6f} @ "
                f"~${proposal.entry_price:.2f} lev={leverage}× "
                f"binance_order={order_id}"
            ),
            signal_id=sig_id, binance_order_id=order_id,
            leverage_chosen=leverage,
        )
    except (OrderRejected, BinanceLiveError) as e:
        log.error("dispatch error for user=%d: %s", user.user_id, e)
        return DispatchResult(
            outcome="error", detail=f"Binance: {e}",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("dispatch unexpected error for user=%d", user.user_id)
        return DispatchResult(
            outcome="error", detail=f"unexpected: {e}",
        )


__all__ = [
    "DispatchOutcome",
    "DispatchResult",
    "SignalProposal",
    "UserContext",
    "dispatch",
]
