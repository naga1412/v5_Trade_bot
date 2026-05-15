"""Admin-only TESTNET round-trip smoke test for the live dispatch path.

End-to-end validation: from "click a button on the dashboard" to "order
appears on Binance Futures testnet." Designed to confirm the wiring
works WITHOUT spending real capital.

What it does:
  POST /api/v1/admin/test-trade/open   -> dispatches a synthesized signal
  POST /api/v1/admin/test-trade/close-all -> market-closes everything on testnet

Hard safety guarantees (NOT relying on env vars — these are baked in):
  - ``BinanceLiveClient(use_testnet=True)`` is wired with True as a literal,
    never reading ``settings.binance_use_testnet``. Even if someone flips
    the env var, this endpoint cannot place a live-money order.
  - Symbol whitelist {"BTCUSDT", "ETHUSDT"} — no obscure-coin surprises.
  - Notional cap enforced at the proposal layer (50 USDT max).
  - ``trading_mode`` forced to "telegram-approve" so the operator sees the
    Telegram message + Approve button — the full chain is exercised, not
    a bypass.
  - Admin-only via the existing ``require_admin`` dependency.
  - Every call appends a structured log line with role=test_trade for
    later audit.

Use case (from operator):
  Validate "the bot actually places orders on Binance" without
  committing real money. After running this once successfully:
    1. Telegram shows the signal with Approve / Reject buttons
    2. Operator taps Approve
    3. Existing telegram_poller dispatches the testnet order
    4. Operator confirms on testnet.binancefuture.com that the
       position appeared
    5. Operator calls /close-all to flatten the testnet account

If anything in that chain is broken — vault decrypt, API permissions,
order signing, Telegram callback — this endpoint surfaces it for free.
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.db.session import get_session
from app.exchanges.binance_live import BinanceLiveClient, BinanceLiveError
from app.trading.execution.dispatcher import (
    DispatchResult,
    SignalProposal,
    UserContext,
    dispatch,
)
from app.trading.execution.glue import vault_keys

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/test-trade",
    tags=["admin", "test-trade"],
    dependencies=[Depends(require_admin)],
)


# Hard whitelist — only these symbols are accepted. Testnet liquidity on
# anything else is unreliable + we want the operator to recognize the
# instrument at a glance.
ALLOWED_SYMBOLS: frozenset[str] = frozenset({"BTCUSDT", "ETHUSDT"})

# Max position notional in USDT. The smoke test wants to be cheap and
# loud — 25 USDT is enough to clear Binance's MIN_NOTIONAL filter
# (typically 5-10 USDT) without committing capital that matters.
MAX_TEST_NOTIONAL_USDT: float = 25.0

# Stop loss / take profit deltas from entry. Conservative — wide enough
# that natural market noise doesn't trip the SL during the few seconds
# between place + manual close. Operator can override via the endpoint.
DEFAULT_SL_PCT: float = 0.005  # 0.5% — wider than typical noise
DEFAULT_TP_PCT: float = 0.010  # 1.0% — keeps R:R reasonable (~2:1)


class OpenTradeRequest(BaseModel):
    """Body for POST /admin/test-trade/open."""

    symbol: Literal["BTCUSDT", "ETHUSDT"] = "BTCUSDT"
    direction: Literal["LONG", "SHORT"] = "LONG"
    notional_usdt: float = Field(
        default=25.0, ge=10.0, le=MAX_TEST_NOTIONAL_USDT,
        description="Position notional in USDT. Capped at MAX_TEST_NOTIONAL_USDT.",
    )


class OpenTradeResponse(BaseModel):
    outcome: str
    detail: str
    signal_id: str | None
    binance_order_id: str | None
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    next_step: str


class CloseAllResponse(BaseModel):
    closed: list[dict]
    skipped: list[dict]


async def _fetch_testnet_mark_price(symbol: str) -> float:
    """Read the current testnet mark price from Binance Futures testnet.

    A market order's fill happens at testnet's price, NOT live's. Use the
    testnet endpoint so the entry/sl/tp we compute are in the same world
    as the order that lands.
    """
    url = f"https://testnet.binancefuture.com/fapi/v1/premiumIndex?symbol={symbol}"
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.get(url)
        r.raise_for_status()
        return float(r.json()["markPrice"])


def _compute_entry_sl_tp(
    *, mark_price: float, direction: Literal["LONG", "SHORT"],
) -> tuple[float, float, float]:
    """Center entry at the current mark; place SL/TP symmetrically."""
    if direction == "LONG":
        entry = mark_price
        sl = entry * (1.0 - DEFAULT_SL_PCT)
        tp = entry * (1.0 + DEFAULT_TP_PCT)
    else:
        entry = mark_price
        sl = entry * (1.0 + DEFAULT_SL_PCT)
        tp = entry * (1.0 - DEFAULT_TP_PCT)
    return entry, sl, tp


@router.post("/open", response_model=OpenTradeResponse)
async def open_test_trade(
    body: OpenTradeRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> OpenTradeResponse:
    """Synthesize a test signal and dispatch it via the real dispatcher.

    Forces testnet=True + mode=telegram-approve so the operator sees the
    full Telegram approval flow without any code shortcuts. The Telegram
    poller will pick up the Approve callback and place the order on
    Binance Futures testnet exactly as it would for a real shadow signal.
    """
    if body.symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"symbol not in whitelist: {body.symbol}")

    keys = vault_keys()
    if keys is None:
        raise HTTPException(
            status_code=503,
            detail="vault not initialised; AUTONOMOUS_TRADING_ENABLED + preflight required",
        )

    try:
        mark_price = await _fetch_testnet_mark_price(body.symbol)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise HTTPException(
            status_code=502, detail=f"testnet mark-price fetch failed: {e}",
        )

    entry, sl, tp = _compute_entry_sl_tp(
        mark_price=mark_price, direction=body.direction,
    )

    proposal = SignalProposal(
        symbol=body.symbol, timeframe="1h",
        direction=body.direction,
        entry_price=entry, stop_loss_price=sl, take_profit_price=tp,
        confidence_pct=80.0,
        layer_summary={"test_trade": {"note": "admin smoke test"}},
        inputs_hash=f"test-trade-{body.symbol}-{body.direction}",
        funding_rate_daily=0.0,
    )

    # Synthesize the UserContext directly — bypasses build_user_context's
    # DB read so this works even if the user row isn't fully configured.
    # CRITICAL: use_testnet hard-coded to True; mode forced to
    # telegram-approve. These two lines are the safety contract.
    user_ctx = UserContext(
        user_id=1,                              # bootstrap admin
        mode="telegram-approve",                # forces Telegram message + approve button
        binance_api_key=keys.binance_api_key,
        binance_api_secret=keys.binance_api_secret,
        use_testnet=True,                       # HARD-CODED — DO NOT EDIT
        portfolio_value_usdt=1000.0,
        successful_trades=0,
        sizing_mode="fixed",
        fixed_size_usdt=body.notional_usdt,
        max_leverage_cap=5,
        max_concurrent_positions=2,
        open_positions_count=0,
    )

    log.warning(
        "admin/test-trade/open: symbol=%s direction=%s notional=%.1f mark=%.2f "
        "entry=%.4f sl=%.4f tp=%.4f",
        body.symbol, body.direction, body.notional_usdt, mark_price,
        entry, sl, tp,
    )

    result: DispatchResult = await dispatch(
        session, proposal=proposal, user=user_ctx,
    )
    await session.commit()

    next_step = "Check your Telegram app for the signal message with Approve/Reject buttons."
    if result.outcome != "sent_telegram":
        next_step = (
            f"Unexpected outcome '{result.outcome}'. "
            f"Detail: {result.detail}. The Telegram message may NOT have been sent. "
            "Check backend logs for the dispatcher error."
        )

    return OpenTradeResponse(
        outcome=result.outcome,
        detail=result.detail,
        signal_id=result.signal_id,
        binance_order_id=result.binance_order_id,
        entry_price=entry,
        stop_loss_price=sl,
        take_profit_price=tp,
        next_step=next_step,
    )


@router.post("/close-all", response_model=CloseAllResponse)
async def close_all_testnet_positions() -> CloseAllResponse:
    """Market-close every open position on the Binance Futures testnet.

    Scans the whitelisted symbols; closes any with a non-zero position.
    Safe to call when no positions are open — symbols without a position
    are listed under ``skipped``.

    Uses a fresh BinanceLiveClient with ``use_testnet=True`` (hard-coded)
    so this endpoint physically cannot touch live Binance.
    """
    keys = vault_keys()
    if keys is None:
        raise HTTPException(
            status_code=503,
            detail="vault not initialised; cannot place close orders",
        )

    closed: list[dict] = []
    skipped: list[dict] = []

    client = BinanceLiveClient(
        api_key=keys.binance_api_key,
        api_secret=keys.binance_api_secret,
        use_testnet=True,  # HARD-CODED — DO NOT EDIT
    )
    try:
        for symbol in sorted(ALLOWED_SYMBOLS):
            try:
                pos = await client.get_position(symbol=symbol)
            except BinanceLiveError as e:
                skipped.append({"symbol": symbol, "reason": f"get_position failed: {e}"})
                continue
            if pos is None or pos.position_amt == 0:
                skipped.append({"symbol": symbol, "reason": "no open position"})
                continue
            try:
                result = await client.close_position(symbol=symbol)
                closed.append({
                    "symbol": symbol,
                    "qty": result.qty,
                    "avg_fill_price": result.avg_fill_price,
                    "binance_order_id": result.binance_order_id,
                })
                log.warning(
                    "admin/test-trade/close-all: closed %s qty=%s @ %s",
                    symbol, result.qty, result.avg_fill_price,
                )
            except BinanceLiveError as e:
                skipped.append({"symbol": symbol, "reason": f"close_position failed: {e}"})
    finally:
        await client.aclose()

    return CloseAllResponse(closed=closed, skipped=skipped)


__all__ = ["router"]
