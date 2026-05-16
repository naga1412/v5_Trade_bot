"""Deterministic fully-auto dispatch test against LIVE Binance Futures.

Operator authorized 2026-05-16: they want to validate the
``fully-auto`` mode end-to-end WITHOUT waiting for the shadow_worker to
organically produce a qualifying signal. Scores have been neutral for
24h+ (range roughly -0.23 to +0.16, never crossing the ±0.30 gate), so
an organic signal could be hours or days away. This script forces the
exact dispatcher → ``_place_live_order`` path that autonomous trading
would use, with a deliberately synthesized proposal.

Difference vs ``test_trade_live_round_trip.py``:
  - round_trip uses BinanceLiveClient directly (validates raw API).
  - this one goes through ``dispatch()`` → ``_place_live_order``, which:
      * re-reads users.trading_mode (we set it to fully-auto first)
      * computes leverage via ``recommended_leverage(...)``
      * calls ``set_leverage`` + ``place_order``
      * writes a ``live_trades`` row with audit hash chain
      * returns ``DispatchResult(outcome="placed", binance_order_id=...)``
  - That's the EXACT path the autonomous worker would use, so a green
    result here means the autonomous path is also green.

Flow:
  1. Init vault (subprocess has no cache).
  2. Pre-flight: refuse if BTCUSDT position already open.
  3. Save current users.trading_mode → temp-flip to 'fully-auto'.
  4. Build a realistic SignalProposal (BTCUSDT LONG, entry = current
     mark, SL -0.5%, TP +1%, margin 5 USDT @ 10x).
  5. Call dispatch(). Expected: outcome='placed', binance_order_id set.
  6. Sleep 3s for order to fully fill.
  7. Close the position via BinanceLiveClient.close_position.
  8. finally: restore the original users.trading_mode.

Safety:
  - ``use_testnet`` is read from settings.binance_use_testnet — which
    is ``false`` on prod RIGHT NOW (we set it earlier for the enable
    probe). So orders route to LIVE Binance. This script knows + names
    that fact in its output so the operator sees it clearly.
  - finally block restores mode even on exception.
  - Pre-flight refuses if a BTCUSDT position exists so we never stack.
  - Position size is a LITERAL (5 USDT margin, 10× leverage = $50
    notional). No env override.

Real-money exposure: ~$5 margin, ~$0.04 fees round-trip, hold ≤ 3s.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Path setup so `from app...` resolves.
for _candidate in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break


# ---- Trade parameters — literals, no overrides ---------------------------

SYMBOL: str = "BTC/USDT"          # dispatcher uses slash form
DIRECTION: str = "LONG"
MARGIN_USDT: float = 5.0           # uses ~$5 of operator's ~$16 balance
LEVERAGE_HINT: int = 10            # caller-supplied leverage (the dispatcher
                                   # may cap this lower via recommended_leverage)
HOLD_SECONDS: float = 3.0
SL_PCT: float = 0.005              # 0.5% — wide enough that natural noise
                                   # in 3s won't trip it
TP_PCT: float = 0.01               # 1% — preserves a sensible R:R for the
                                   # dispatcher's leverage recommender


async def main() -> int:
    import os
    import httpx
    import sqlalchemy as sa
    from app.config import get_settings
    from app.db.session import get_session_factory
    from app.exchanges.binance_live import BinanceLiveClient, BinanceLiveError
    from app.trading.execution.dispatcher import (
        SignalProposal, UserContext, dispatch,
    )
    from app.trading.execution.glue import initialize_vault_cache, vault_keys

    # Vault init.
    settings = get_settings()
    secrets_path = Path(
        os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"),
    )
    if not initialize_vault_cache(
        passphrase=settings.master_passphrase, secrets_path=secrets_path,
    ):
        print(f"FAIL: vault init from {secrets_path}", file=sys.stderr)
        return 1
    keys = vault_keys()
    if keys is None:
        print("FAIL: vault_keys() returned None", file=sys.stderr)
        return 1

    use_testnet = settings.binance_use_testnet
    env_label = "TESTNET" if use_testnet else "LIVE"

    print("=== Parameters (LITERALS) ===")
    print(f"symbol     = {SYMBOL}")
    print(f"direction  = {DIRECTION}")
    print(f"margin     = {MARGIN_USDT} USDT")
    print(f"leverage   = up to {LEVERAGE_HINT}x (dispatcher may cap lower)")
    print(f"hold       = {HOLD_SECONDS}s")
    print(f"sl         = {SL_PCT*100:.2f}% from entry")
    print(f"tp         = {TP_PCT*100:.2f}% from entry")
    print(f"routing    = {env_label}  (settings.binance_use_testnet={use_testnet})")

    # Pre-flight: refuse if BTCUSDT position already open.
    pre_check_client = BinanceLiveClient(
        api_key=keys.binance_api_key,
        api_secret=keys.binance_api_secret,
        use_testnet=use_testnet,
    )
    try:
        existing = await pre_check_client.get_position(symbol="BTCUSDT")
        if existing is not None:
            print(
                f"\nABORT: existing BTCUSDT position "
                f"amt={existing.position_amt} entry={existing.entry_price} "
                f"lev={existing.leverage}x. Refusing to stack.",
            )
            return 2
    finally:
        await pre_check_client.aclose()

    # Fetch current mark price for entry.
    fetch_url = (
        "https://testnet.binancefuture.com" if use_testnet
        else "https://fapi.binance.com"
    ) + "/fapi/v1/premiumIndex?symbol=BTCUSDT"
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.get(fetch_url)
        r.raise_for_status()
        mark = float(r.json()["markPrice"])

    entry = mark
    sl = entry * (1.0 - SL_PCT) if DIRECTION == "LONG" else entry * (1.0 + SL_PCT)
    tp = entry * (1.0 + TP_PCT) if DIRECTION == "LONG" else entry * (1.0 - TP_PCT)
    print(f"\n=== Mark + computed entry/sl/tp ===")
    print(f"mark={mark:.2f}  entry={entry:.2f}  sl={sl:.4f}  tp={tp:.4f}")

    proposal = SignalProposal(
        symbol=SYMBOL,
        timeframe="1h",
        direction=DIRECTION,  # type: ignore[arg-type]
        entry_price=entry,
        stop_loss_price=sl,
        take_profit_price=tp,
        confidence_pct=80.0,
        layer_summary={"test": {"note": "ops-debug fully-auto round-trip"}},
        inputs_hash="ops-debug-fully-auto-roundtrip",
        funding_rate_daily=0.0,
    )
    user_ctx = UserContext(
        user_id=1,
        mode="fully-auto",                          # not used (DB wins)
        binance_api_key=keys.binance_api_key,
        binance_api_secret=keys.binance_api_secret,
        use_testnet=use_testnet,
        portfolio_value_usdt=16.0,
        successful_trades=0,
        sizing_mode="fixed",
        fixed_size_usdt=MARGIN_USDT,
        max_leverage_cap=LEVERAGE_HINT,
        max_concurrent_positions=2,
        open_positions_count=0,
    )

    factory = get_session_factory()
    placed_order_id: str | None = None

    async with factory() as session:
        # Read + flip + remember.
        original_row = await session.execute(
            sa.text("SELECT trading_mode FROM users WHERE id = 1"),
        )
        original_mode = original_row.scalar() or "manual"
        print(f"\n=== Mode flip ===")
        print(f"original users.trading_mode = {original_mode}")

        try:
            await session.execute(
                sa.text(
                    "UPDATE users SET trading_mode = 'fully-auto' "
                    "WHERE id = 1",
                ),
            )
            await session.commit()
            print("flipped users.trading_mode -> 'fully-auto'")

            # ---- DISPATCH ----
            print(f"\n=== DISPATCH (fully-auto path) ===")
            result = await dispatch(session, proposal=proposal, user=user_ctx)
            await session.commit()
            print(f"outcome          = {result.outcome}")
            print(f"detail           = {result.detail}")
            print(f"signal_id        = {result.signal_id}")
            print(f"binance_order_id = {result.binance_order_id}")
            print(f"leverage_chosen  = {result.leverage_chosen}")

            if result.outcome != "placed":
                print(
                    f"\nFAIL: expected outcome='placed' but got "
                    f"'{result.outcome}'. Detail above tells you why "
                    f"(kill switch, funding guard, etc).",
                )
                return 3

            placed_order_id = result.binance_order_id

            # ---- HOLD ----
            await asyncio.sleep(HOLD_SECONDS)

            # ---- CLOSE (via BinanceLiveClient — dispatcher doesn't auto-close) ----
            close_client = BinanceLiveClient(
                api_key=keys.binance_api_key,
                api_secret=keys.binance_api_secret,
                use_testnet=use_testnet,
            )
            try:
                close = await close_client.close_position(symbol="BTCUSDT")
                print(f"\n=== CLOSE (reduceOnly market) ===")
                print(f"order_id        = {close.binance_order_id}")
                print(f"qty             = {close.qty}")
                print(f"avg_fill_price  = {close.avg_fill_price}")
                print(f"status          = {close.status}")
            finally:
                await close_client.aclose()

            return 0

        except BinanceLiveError as e:
            print(f"\nBINANCE ERROR: {e}")
            return 1
        except Exception as e:  # noqa: BLE001
            print(f"\nUNEXPECTED ERROR: {type(e).__name__}: {e}")
            return 1
        finally:
            # Restore mode regardless.
            try:
                await session.execute(
                    sa.text(
                        "UPDATE users SET trading_mode = :m WHERE id = 1",
                    ),
                    {"m": original_mode},
                )
                await session.commit()
                print(f"\nrestored users.trading_mode -> '{original_mode}'")
            except Exception as restore_err:  # noqa: BLE001
                print(
                    f"\n⚠ MODE RESTORE FAILED: {restore_err}. "
                    f"users.trading_mode is currently 'fully-auto' — "
                    f"flip it back via: "
                    f"docker compose exec postgres psql -U postgres -d "
                    f"trading_radar -c \"UPDATE users SET trading_mode='"
                    f"{original_mode}' WHERE id=1;\"",
                    file=sys.stderr,
                )

            # Safety: if we placed but didn't see a successful close, try
            # once more to flatten.
            if placed_order_id is not None:
                try:
                    safety_client = BinanceLiveClient(
                        api_key=keys.binance_api_key,
                        api_secret=keys.binance_api_secret,
                        use_testnet=use_testnet,
                    )
                    try:
                        still_open = await safety_client.get_position(symbol="BTCUSDT")
                        if still_open is not None:
                            rescue = await safety_client.close_position(symbol="BTCUSDT")
                            print(
                                f"\nfinally: SAFETY CLOSE order_id="
                                f"{rescue.binance_order_id} fill="
                                f"{rescue.avg_fill_price}",
                            )
                    finally:
                        await safety_client.aclose()
                except Exception as rescue_err:  # noqa: BLE001
                    print(
                        f"\n⚠⚠⚠ MANUAL ACTION NEEDED ⚠⚠⚠\n"
                        f"Failed to flatten BTCUSDT in finally: {rescue_err}. "
                        f"Open Binance app and close the position by hand.",
                        file=sys.stderr,
                    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
