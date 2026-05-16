"""LIVE Binance Futures round-trip smoke test (REAL money).

Authorized by the operator on 2026-05-16 after the testnet path
revealed the vaulted key is a LIVE Binance key (not testnet) and the
operator wanted to validate end-to-end against their own $16 Futures
balance.

What this script does, in one atomic execution:
  1. Init the vault (re-decrypt secrets.enc in the subprocess).
  2. HARD-CODE ``use_testnet=False`` and construct the LIVE client.
  3. Pre-flight: refuse if a position on BTCUSDT already exists (we do
     NOT stack on top of an existing position the operator may have
     opened by hand).
  4. Place a SINGLE MARKET BUY: 0.001 BTC, 20× leverage.
       - Notional ≈ 0.001 × current price (~$79 at the time of writing).
       - Margin ≈ notional / 20 ≈ $4 of the operator's ~$16 balance.
       - Fees ≈ notional × 0.04% × 2 (round trip) ≈ $0.06.
  5. Sleep 3 seconds. Long enough for the order to fully fill, short
     enough that market noise stays well under 0.1%.
  6. Market-close the position (reduceOnly SELL).
  7. Print open + close fills + approximate gross P&L.

Safety nets:
  - The entire body is wrapped in try / finally. If anything raises
    AFTER the open succeeds, the finally block attempts close_position
    one more time. Failing to close prints a loud MANUAL ACTION NEEDED
    line so the operator knows to flatten via the Binance app.
  - Pre-flight rejects if get_position returns a non-None state.
  - Quantity + leverage are LITERALS in this file. There is no
    parameter, no env override, no DB read for sizing. The exposure
    is what's written here, full stop.

This script is INVOKED ONLY by the ops-debug ``test-trade-live-open``
probe, which requires explicit operator authorization in the agent's
turn. It is not on any cron, not on any background task. Real-money
exposure happens ONLY when the probe is triggered manually.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Path setup so `from app...` resolves both in repo + container.
for _candidate in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break

# ---- LIVE trade parameters — literals, no overrides ---------------------

LIVE_SYMBOL: str = "BTCUSDT"
LIVE_SIDE: str = "BUY"
LIVE_QUANTITY: float = 0.001   # Binance Futures minimum for BTCUSDT
LIVE_LEVERAGE: int = 20        # 20x → margin = notional / 20 ≈ $4 on $16 balance
HOLD_SECONDS: float = 3.0


async def main() -> int:
    import os
    from app.config import get_settings
    from app.exchanges.binance_live import BinanceLiveClient, BinanceLiveError
    from app.trading.execution.glue import initialize_vault_cache, vault_keys

    # Vault init — subprocess doesn't share uvicorn's in-memory cache.
    settings = get_settings()
    secrets_path = Path(
        os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"),
    )
    if not initialize_vault_cache(
        passphrase=settings.master_passphrase, secrets_path=secrets_path,
    ):
        print(
            f"FAIL: initialize_vault_cache failed in {secrets_path}",
            file=sys.stderr,
        )
        return 1
    keys = vault_keys()
    if keys is None:
        print("FAIL: vault_keys() returned None after init", file=sys.stderr)
        return 1

    print("=== Parameters (LITERALS in this file — not configurable) ===")
    print(f"symbol     = {LIVE_SYMBOL}")
    print(f"side       = {LIVE_SIDE}")
    print(f"quantity   = {LIVE_QUANTITY}  (BTC)")
    print(f"leverage   = {LIVE_LEVERAGE}x")
    print(f"hold       = {HOLD_SECONDS}s")
    print(f"use_testnet= False  (LIVE Binance)")

    client = BinanceLiveClient(
        api_key=keys.binance_api_key,
        api_secret=keys.binance_api_secret,
        use_testnet=False,  # HARD-CODED LIVE
    )

    open_order = None
    try:
        # Pre-flight: do not stack on an existing position.
        existing = await client.get_position(symbol=LIVE_SYMBOL)
        if existing is not None:
            print(
                f"\nABORT: existing {LIVE_SYMBOL} position detected — "
                f"position_amt={existing.position_amt} "
                f"entry={existing.entry_price} lev={existing.leverage}x. "
                f"Refusing to stack. Close it manually on Binance, then retry.",
            )
            return 2

        # ---- OPEN ----
        print(f"\n=== OPEN: MARKET {LIVE_SIDE} {LIVE_QUANTITY} {LIVE_SYMBOL} @ {LIVE_LEVERAGE}x ===")
        open_order = await client.place_order(
            symbol=LIVE_SYMBOL,
            side=LIVE_SIDE,                  # type: ignore[arg-type]
            quantity=LIVE_QUANTITY,
            leverage=LIVE_LEVERAGE,
            order_type="MARKET",
        )
        print(f"OPEN OK:")
        print(f"  order_id        = {open_order.binance_order_id}")
        print(f"  qty             = {open_order.qty}")
        print(f"  avg_fill_price  = {open_order.avg_fill_price}")
        print(f"  status          = {open_order.status}")

        # ---- HOLD ----
        await asyncio.sleep(HOLD_SECONDS)

        # ---- CLOSE ----
        print(f"\n=== CLOSE: market-close {LIVE_SYMBOL} (reduceOnly) ===")
        close_order = await client.close_position(symbol=LIVE_SYMBOL)
        print(f"CLOSE OK:")
        print(f"  order_id        = {close_order.binance_order_id}")
        print(f"  qty             = {close_order.qty}")
        print(f"  avg_fill_price  = {close_order.avg_fill_price}")
        print(f"  status          = {close_order.status}")

        # ---- P&L (approximate, gross of fees) ----
        if open_order.avg_fill_price and close_order.avg_fill_price:
            pnl_per_unit = close_order.avg_fill_price - open_order.avg_fill_price
            if LIVE_SIDE == "SELL":
                pnl_per_unit = -pnl_per_unit
            pnl = pnl_per_unit * open_order.qty
            print(f"\nGross P&L (excl. fees): ${pnl:.4f}")
            print(f"Estimated fees:         ~$0.06 (0.04% × 2 × notional)")
        return 0

    except BinanceLiveError as e:
        msg = str(e)
        print(f"\nBINANCE ERROR: {msg}")
        if "-2015" in msg:
            print(
                "  → -2015 means: Invalid API-key, IP, or Futures-Trade "
                "permission. Check the Binance API key settings.",
            )
        elif "-2019" in msg:
            print(
                "  → -2019 means: Margin is insufficient. The $4 margin "
                "this trade needs exceeds the operator's available balance "
                "(probably because other open positions are locking it).",
            )
        elif "-1102" in msg or "-4061" in msg:
            print(
                "  → Position mode mismatch. The account is in hedge mode "
                "but this code sends one-way orders (or vice versa). Adjust "
                "in Binance UI: Settings → Preferences → Position Mode.",
            )
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\nUNEXPECTED ERROR: {type(e).__name__}: {e}")
        return 1
    finally:
        # If we opened but didn't close (any exception path), try once
        # more so we don't leave a real-money position open.
        if open_order is not None:
            try:
                still_open = await client.get_position(symbol=LIVE_SYMBOL)
                if still_open is not None:
                    print(
                        f"\nfinally: position still open — "
                        f"attempting safety close...",
                    )
                    rescue = await client.close_position(symbol=LIVE_SYMBOL)
                    print(
                        f"finally: SAFETY CLOSE order_id="
                        f"{rescue.binance_order_id} fill="
                        f"{rescue.avg_fill_price}",
                    )
            except Exception as rescue_err:  # noqa: BLE001
                print(
                    f"\n⚠⚠⚠ MANUAL ACTION NEEDED ⚠⚠⚠\n"
                    f"Failed to flatten {LIVE_SYMBOL} position in finally: "
                    f"{rescue_err}. Open the Binance app and close the "
                    f"position by hand.",
                    file=sys.stderr,
                )
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
