"""PR-FIX-PR275-FOLLOWUP Part 2: end-to-end real-money test trade.

Validates the full atomic_placement chain against live Binance Futures:
  1. INSERT telegram_signals row
  2. Call `_place_approved_order` directly (bypass Telegram callback —
     same code path)
  3. Verify live_trades row reached status='open' with sl_order_id,
     tp_order_id, binance_order_id all populated
  4. Verify Binance position has STOP_MARKET + TAKE_PROFIT_MARKET
     orders attached via openOrders
  5. CLOSE the position (market reduce-only)
  6. Verify live_trades.closed_at set, exit_reason recorded
  7. Print a structured JSON report

USAGE (Hetzner host)
====================
::

  cd /opt/trading-radar
  docker compose exec -T backend python -m tools.test_atomic_placement_round_trip

Safety:
  * Position size HARD-CAPPED to $10 margin × 20 leverage = $200 notional.
  * SL/TP at ±0.5% from the live mark price (tight to limit exposure
    if the close path itself bugs out).
  * Auto-aborts (and reports) if any of the abort conditions fires;
    leaves the position open ONLY if `close_position` itself raises
    (the operator must intervene — message will say so loudly).

ABORT CONDITIONS (script bails out with a clear message)
========================================================
* `HYBRID_AUTO_SCORE_THRESHOLD` < 0.9 (interpreted as: safety freeze is
  lifted — operator should run this script themselves, not delegate).
  The threshold is on a fraction scale (0, 1) — Pydantic validator at
  `app/config.py` rejects values >= 1.0 or <= 0. Earlier draft of this
  script used `< 90` which can NEVER pass, blocking the safety check
  entirely. Fixed 2026-05-27 per PR-FIX-PR275-FOLLOWUP.
* Vault keys not loaded (need real Binance creds; testnet won't
  exercise the real Binance order pipeline)
* `_place_approved_order` returns None → indicates Part 1 fix didn't
  land OR there's another bug
* Binance returns 4xx auth error on any call
* Position fails to close cleanly within 60 seconds
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa


# Lazy imports — only after the script has confirmed it's running in
# the right environment. Catch import errors loudly so the operator
# knows it's an env issue, not a logic bug.
def _safe_import() -> dict[str, Any]:
    try:
        from app.config import get_settings
        from app.db.session import get_session_factory
        from app.exchanges.binance_live import BinanceLiveClient
        from app.ops.telegram_polling import _place_approved_order
        from app.trading.execution.glue import vault_keys
        return {
            "get_settings": get_settings,
            "get_session_factory": get_session_factory,
            "BinanceLiveClient": BinanceLiveClient,
            "_place_approved_order": _place_approved_order,
            "vault_keys": vault_keys,
        }
    except Exception as e:  # noqa: BLE001
        print(json.dumps({
            "status": "abort",
            "phase": "import",
            "error": f"{type(e).__name__}: {e}",
            "hint": "Run from /opt/trading-radar via "
                    "`docker compose exec backend python -m tools.test_atomic_placement_round_trip`",
        }, indent=2))
        sys.exit(2)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("test_atomic_placement_round_trip")

_USER_ID = 1                       # bootstrap admin
_MARGIN_USDT = 10.0                # hard-capped
_LEVERAGE = 20                     # so margin × lev = $200 ≥ Binance min_notional
_DRIFT_FROM_MID_PCT = 0.5          # ±0.5% SL/TP — tight
_BINANCE_NATIVE_SYMBOL = "BTCUSDT"
_PAIR_SYMBOL = "BTC/USDT"
_CLOSE_TIMEOUT_SECONDS = 60


async def _fetch_btc_mid_price(http: httpx.AsyncClient) -> float:
    """Public Binance Futures ticker — no auth required."""
    r = await http.get(
        "https://fapi.binance.com/fapi/v1/ticker/price",
        params={"symbol": _BINANCE_NATIVE_SYMBOL},
        timeout=5.0,
    )
    r.raise_for_status()
    return float(r.json()["price"])


async def _insert_test_signal(
    session_factory: Any,
    *,
    signal_id: str,
    entry: float, sl: float, tp: float,
    margin: float,
    now: datetime,
) -> None:
    """Create a synthetic telegram_signals row that
    `_place_approved_order` will read by signal_id."""
    payload = {
        "symbol": _PAIR_SYMBOL,
        "timeframe": "1h",
        "direction": "LONG",
        "entry_price": entry,
        "stop_loss_price": sl,
        "take_profit_price": tp,
        "confidence_pct": 80.0,
        "margin_usdt": margin,
        "funding_rate_daily": 0.0,
        "rendered_body": "(test_atomic_placement_round_trip)",
        "inline_keyboard": [],
        "inputs_hash": "test_round_trip",
        # No mtf_directions — the new code path handles None gracefully.
    }
    async with session_factory() as s:
        await s.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload, response) "
            "VALUES (:id, :u, :sym, 'LONG', :ts, :p, NULL)"
        ), {
            "id": signal_id, "u": _USER_ID, "sym": _PAIR_SYMBOL,
            "ts": now, "p": json.dumps(payload),
        })
        await s.commit()


async def _fetch_live_trade_row(
    session_factory: Any, *, signal_id: str,
) -> dict[str, Any] | None:
    """Find the live_trades row produced by _place_approved_order via
    the signal_id stored in the reasoning JSON. Returns the full
    inspectable shape so the report has everything the operator wants."""
    async with session_factory() as s:
        row = (await s.execute(sa.text(
            "SELECT id, status, binance_order_id, sl_order_id, tp_order_id, "
            "       entry_price, stop_loss, take_profit, margin_usdt, leverage, "
            "       opened_at, closed_at, exit_price, exit_reason, pnl_usdt, "
            "       failure_reason "
            "FROM live_trades "
            "WHERE reasoning::text LIKE :pat "
            "ORDER BY id DESC LIMIT 1"
        ), {"pat": f"%{signal_id}%"})).first()
    if row is None:
        return None
    return {
        "id": row.id, "status": row.status,
        "binance_order_id": row.binance_order_id,
        "sl_order_id": row.sl_order_id, "tp_order_id": row.tp_order_id,
        "entry_price": float(row.entry_price) if row.entry_price else None,
        "stop_loss": float(row.stop_loss) if row.stop_loss else None,
        "take_profit": float(row.take_profit) if row.take_profit else None,
        "margin_usdt": float(row.margin_usdt) if row.margin_usdt else None,
        "leverage": row.leverage,
        "opened_at": str(row.opened_at) if row.opened_at else None,
        "closed_at": str(row.closed_at) if row.closed_at else None,
        "exit_price": float(row.exit_price) if row.exit_price else None,
        "exit_reason": row.exit_reason,
        "pnl_usdt": float(row.pnl_usdt) if row.pnl_usdt is not None else None,
        "failure_reason": row.failure_reason,
    }


async def _verify_binance_attached_orders(
    client: Any,
) -> dict[str, Any]:
    """Hit /fapi/v1/openOrders for BTCUSDT, return the SL + TP rows
    if present. Used to confirm the orders actually landed on Binance."""
    from app.exchanges.binance_live import _build_signed_query
    params = {"symbol": _BINANCE_NATIVE_SYMBOL}
    query = _build_signed_query(client._secret, params)
    r = await client._http.get(
        f"{client._base}/fapi/v1/openOrders?{query}",
        headers=client.headers,
    )
    r.raise_for_status()
    orders = r.json()
    sl_orders = [o for o in orders if o.get("type") == "STOP_MARKET"]
    tp_orders = [o for o in orders if o.get("type") == "TAKE_PROFIT_MARKET"]
    return {
        "total_open_orders": len(orders),
        "sl_count": len(sl_orders),
        "tp_count": len(tp_orders),
        "sl_orderId": sl_orders[0]["orderId"] if sl_orders else None,
        "tp_orderId": tp_orders[0]["orderId"] if tp_orders else None,
    }


async def _close_position_market(client: Any, side_to_close: str) -> dict[str, Any]:
    """Reduce-only MARKET to close the position. side_to_close is the
    CLOSING side ('SELL' for LONG entry)."""
    from app.exchanges.binance_live import _build_signed_query
    # Need the exact qty (after Binance's quantization). Read it from
    # the current position; if it returns None the position already
    # closed (probably SL/TP filled in the test window).
    pos = await client.get_position(symbol=_BINANCE_NATIVE_SYMBOL)
    if pos is None:
        return {"already_closed": True}
    params = {
        "symbol": _BINANCE_NATIVE_SYMBOL,
        "side": side_to_close,
        "type": "MARKET",
        "quantity": abs(pos.position_amt),
        "reduceOnly": "true",
    }
    query = _build_signed_query(client._secret, params)
    r = await client._http.post(
        f"{client._base}/fapi/v1/order?{query}",
        headers=client.headers,
    )
    r.raise_for_status()
    body = r.json()
    return {
        "already_closed": False,
        "close_order_id": str(body["orderId"]),
        "close_avg_price": float(body.get("avgPrice") or 0),
        "close_qty": float(body.get("executedQty") or 0),
    }


async def _mark_closed_in_db(
    session_factory: Any, *, trade_id: int, exit_price: float, now: datetime,
) -> None:
    async with session_factory() as s:
        await s.execute(sa.text(
            "UPDATE live_trades SET "
            "  status='closed', closed_at=:n, exit_price=:ep, "
            "  exit_reason='test_round_trip_manual_close' "
            "WHERE id=:tid"
        ), {"n": now, "ep": exit_price, "tid": trade_id})
        await s.commit()


async def _run() -> dict[str, Any]:
    start_time = time.time()
    deps = _safe_import()
    get_settings = deps["get_settings"]
    get_session_factory = deps["get_session_factory"]
    BinanceLiveClient = deps["BinanceLiveClient"]
    _place_approved_order = deps["_place_approved_order"]
    vault_keys = deps["vault_keys"]

    # ─── Safety: only run while HYBRID is frozen ──────────────────────
    # The threshold is on the fraction scale (0, 1) — Pydantic validator
    # in app/config.py rejects values <= 0 or >= 1.0. Treat any value
    # < 0.9 as "freeze is lifted, normal trading active, this script
    # should not run autonomously" (operator would invoke directly if
    # they intended a test trade outside the frozen window).
    _SAFETY_FREEZE_FLOOR = 0.9
    settings = get_settings()
    hybrid_threshold = settings.HYBRID_AUTO_SCORE_THRESHOLD
    if hybrid_threshold is None or hybrid_threshold < _SAFETY_FREEZE_FLOOR:
        return {
            "status": "abort",
            "phase": "safety_freeze",
            "reason": (
                f"HYBRID_AUTO_SCORE_THRESHOLD={hybrid_threshold} suggests "
                "the safety freeze is lifted (expected >= 0.9 for a "
                "frozen-state validation run). This script is for the "
                "frozen-state validation only — operator runs it directly "
                "post-freeze. Refusing to proceed."
            ),
        }

    # ─── Safety: vault must be loaded (real Binance creds) ───────────
    keys = vault_keys()
    if keys is None:
        return {
            "status": "abort",
            "phase": "vault",
            "reason": "vault_keys() returned None — Binance creds not loaded",
        }

    # ─── Step 1: fetch live BTC mid price ────────────────────────────
    async with httpx.AsyncClient(timeout=10.0) as http:
        mid = await _fetch_btc_mid_price(http)
    entry = mid
    sl = mid * (1.0 - _DRIFT_FROM_MID_PCT / 100.0)
    tp = mid * (1.0 + _DRIFT_FROM_MID_PCT / 100.0)
    log.info("step 1: BTC mid=%.2f → entry=%.2f sl=%.2f tp=%.2f", mid, entry, sl, tp)

    # ─── Step 2: INSERT synthetic telegram_signals row ────────────────
    session_factory = get_session_factory()
    signal_id = "sig_" + secrets.token_hex(8)
    now = datetime.now(timezone.utc)
    await _insert_test_signal(
        session_factory,
        signal_id=signal_id,
        entry=entry, sl=sl, tp=tp,
        margin=_MARGIN_USDT, now=now,
    )
    log.info("step 2: synthetic signal_id=%s inserted", signal_id)

    # ─── Step 3: call _place_approved_order ──────────────────────────
    def _binance_factory():
        # use_testnet=False because we want the real Binance.
        return BinanceLiveClient(
            api_key=keys.binance_api_key,
            api_secret=keys.binance_api_secret,
            use_testnet=False,
        )

    log.info("step 3: calling _place_approved_order...")
    try:
        order_id = await _place_approved_order(
            session_factory,
            signal_id=signal_id,
            leverage=_LEVERAGE,
            use_testnet=False,
            user_id=_USER_ID,
            binance_factory=_binance_factory,
        )
    except Exception as e:  # noqa: BLE001
        return {
            "status": "abort",
            "phase": "place_approved_order",
            "error": f"{type(e).__name__}: {e}",
            "signal_id": signal_id,
            "live_trade_row": await _fetch_live_trade_row(
                session_factory, signal_id=signal_id,
            ),
        }
    if order_id is None:
        return {
            "status": "abort",
            "phase": "place_approved_order_returned_none",
            "reason": (
                "Indicates either Part 1 fix didn't land (CheckViolation) "
                "OR drift-guard rejected the approval. Check live_trades."
            ),
            "signal_id": signal_id,
            "live_trade_row": await _fetch_live_trade_row(
                session_factory, signal_id=signal_id,
            ),
        }
    log.info("step 3: _place_approved_order returned binance_order_id=%s", order_id)

    # ─── Step 4: verify live_trades row ──────────────────────────────
    trade_row = await _fetch_live_trade_row(
        session_factory, signal_id=signal_id,
    )
    if trade_row is None:
        return {
            "status": "abort",
            "phase": "live_trades_lookup",
            "reason": "live_trades row missing despite order_id returned",
            "signal_id": signal_id,
        }
    if trade_row["status"] != "open":
        return {
            "status": "abort",
            "phase": "lifecycle_not_open",
            "live_trade_row": trade_row,
        }
    log.info(
        "step 4: live_trades row trade_id=%d status=open "
        "sl_order_id=%s tp_order_id=%s",
        trade_row["id"], trade_row["sl_order_id"], trade_row["tp_order_id"],
    )

    # ─── Step 5: verify Binance has SL+TP orders attached ─────────────
    client = _binance_factory()
    try:
        attached = await _verify_binance_attached_orders(client)
    finally:
        await client.aclose()
    log.info("step 5: Binance attached orders = %s", attached)
    if attached["sl_count"] < 1 or attached["tp_count"] < 1:
        return {
            "status": "abort",
            "phase": "binance_orders_missing",
            "trade_id": trade_row["id"],
            "live_trade_row": trade_row,
            "attached": attached,
            "note": (
                "Position open on Binance but SL/TP not attached. "
                "OPERATOR MUST CLOSE MANUALLY."
            ),
        }

    # ─── Step 6: close the position ──────────────────────────────────
    log.info("step 6: closing position via reduce-only MARKET...")
    client = _binance_factory()
    close_start = time.time()
    try:
        close_result = await asyncio.wait_for(
            _close_position_market(client, side_to_close="SELL"),
            timeout=_CLOSE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await client.aclose()
        return {
            "status": "abort",
            "phase": "close_timeout",
            "trade_id": trade_row["id"],
            "live_trade_row": trade_row,
            "elapsed_s": time.time() - close_start,
            "note": "Position close timed out — OPERATOR MUST INTERVENE.",
        }
    finally:
        await client.aclose()
    log.info("step 6: close_result=%s", close_result)

    exit_price = (
        close_result.get("close_avg_price")
        or trade_row["entry_price"] or 0.0
    )

    # ─── Step 7: mark live_trades closed ─────────────────────────────
    closed_at = datetime.now(timezone.utc)
    await _mark_closed_in_db(
        session_factory,
        trade_id=trade_row["id"],
        exit_price=exit_price,
        now=closed_at,
    )

    # ─── Step 8: final fetch ─────────────────────────────────────────
    final_row = await _fetch_live_trade_row(
        session_factory, signal_id=signal_id,
    )
    elapsed_total = time.time() - start_time
    pnl_usdt = None
    if exit_price and trade_row["entry_price"]:
        # LONG: pnl ≈ (exit - entry) × qty
        qty = _MARGIN_USDT * _LEVERAGE / trade_row["entry_price"]
        pnl_usdt = (exit_price - trade_row["entry_price"]) * qty
    return {
        "status": "success",
        "signal_id": signal_id,
        "trade_id": trade_row["id"],
        "lifecycle": {
            "pending_to_open": True,
            "open_to_closed": True,
        },
        "binance": {
            "entry_order_id": trade_row["binance_order_id"],
            "sl_order_id": trade_row["sl_order_id"],
            "tp_order_id": trade_row["tp_order_id"],
            "close_order_id": close_result.get("close_order_id"),
            "attached_at_open": attached,
        },
        "prices": {
            "btc_mid_at_test_start": mid,
            "signal_entry": trade_row["entry_price"],
            "signal_sl": trade_row["stop_loss"],
            "signal_tp": trade_row["take_profit"],
            "actual_close_price": exit_price,
        },
        "pnl_estimate_usdt": round(pnl_usdt, 4) if pnl_usdt is not None else None,
        "elapsed_seconds_total": round(elapsed_total, 2),
        "final_live_trade_row": final_row,
    }


def main() -> None:
    result = asyncio.run(_run())
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
