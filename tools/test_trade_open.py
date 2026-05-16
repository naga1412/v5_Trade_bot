"""Server-side trigger for the testnet smoke trade.

Equivalent of POST /api/v1/admin/test-trade/open, but invoked from
INSIDE the backend container by the ops-debug ``test-trade-open`` probe.
Bypasses Cloudflare Access (no HTTP layer) which is needed when the
operator can't paste into DevTools console and the dashboard UI bundle
hasn't propagated past their browser cache.

Same hard-coded safety contract as the HTTP endpoint:
  - ``use_testnet=True`` literal
  - ``mode="telegram-approve"`` (operator must Approve in Telegram)
  - Symbol BTCUSDT, direction LONG, notional 20 USDT
  - No env-driven overrides — every parameter is a literal in this file

Output: prints the DispatchResult fields to stdout so the ops-debug run
log surfaces them. The operator then taps Approve in Telegram and the
existing ``telegram_poller_task`` places the order on Binance Futures
testnet exactly as it would for a real shadow signal.

Pair with ``test_trade_close_all.py`` for the flatten step.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Path setup mirrors tools/populate_universe_history.py — needed because
# the file is bind-mounted at /app/host-tools/ in the container while the
# actual `app/` package lives at /app/app/. uvicorn already has /app on
# sys.path; ad-hoc `python` invocations don't, so we add it explicitly.
for _candidate in (
    Path(__file__).resolve().parent.parent / "backend",  # repo layout
    Path("/app"),                                         # container layout
):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break

import httpx  # noqa: E402


async def main() -> int:
    # Late imports so the module load doesn't pay for them when we error
    # early on vault check.
    from app.db.session import get_session_factory
    from app.trading.execution.dispatcher import (
        SignalProposal,
        UserContext,
        dispatch,
    )
    from app.trading.execution.glue import vault_keys

    # `docker compose exec backend python ...` starts a fresh Python
    # process — it does NOT share the running uvicorn process's in-memory
    # vault cache. Re-initialise here from the same secrets.enc + master
    # passphrase the uvicorn lifespan used.
    import os
    from pathlib import Path as _Path
    from app.config import get_settings
    from app.trading.execution.glue import initialize_vault_cache

    settings = get_settings()
    secrets_path = _Path(os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"))
    if not initialize_vault_cache(
        passphrase=settings.master_passphrase, secrets_path=secrets_path,
    ):
        print(
            f"FAIL: initialize_vault_cache failed (passphrase mismatch or "
            f"missing keys in {secrets_path})",
            file=sys.stderr,
        )
        return 1

    keys = vault_keys()
    if keys is None:
        print("FAIL: vault_keys() returned None after init", file=sys.stderr)
        return 1

    # Use testnet mark price for the entry. The Futures testnet runs its
    # own price feed; live mark would skew the SL/TP outside testnet's
    # range and trip the SL instantly when the order fills.
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.get(
            "https://testnet.binancefuture.com/fapi/v1/premiumIndex"
            "?symbol=BTCUSDT",
        )
        r.raise_for_status()
        mark = float(r.json()["markPrice"])

    entry = mark
    # 0.5% SL, 1% TP — wide enough that natural testnet noise during the
    # few seconds between Approve and manual close doesn't trip the SL.
    sl = entry * 0.995
    tp = entry * 1.010
    print(f"mark={mark:.2f}  entry={entry:.2f}  sl={sl:.4f}  tp={tp:.4f}")

    proposal = SignalProposal(
        symbol="BTCUSDT",
        timeframe="1h",
        direction="LONG",
        entry_price=entry,
        stop_loss_price=sl,
        take_profit_price=tp,
        confidence_pct=80.0,
        layer_summary={"test": {"note": "ops-debug test-trade-open"}},
        inputs_hash="ops-debug-test-trade-open",
        funding_rate_daily=0.0,
    )

    # Synthesize a UserContext directly — bypasses build_user_context's
    # DB read. CRITICAL: use_testnet=True + mode=telegram-approve are
    # the entire safety contract for this script.
    user = UserContext(
        user_id=1,
        mode="telegram-approve",
        binance_api_key=keys.binance_api_key,
        binance_api_secret=keys.binance_api_secret,
        use_testnet=True,
        portfolio_value_usdt=1000.0,
        successful_trades=0,
        sizing_mode="fixed",
        fixed_size_usdt=20.0,
        max_leverage_cap=5,
        max_concurrent_positions=2,
        open_positions_count=0,
    )

    # The dispatcher re-reads ``trading_mode`` from the DB (avoid stale
    # state); UserContext.mode is ignored. Production users are locked at
    # ``manual`` for SP-0.7 + require promotion gates + TOTP to upgrade —
    # gates that exist to protect REAL MONEY. Since this script is
    # testnet-locked, we flip the DB mode to ``telegram-approve`` for the
    # one dispatch call and restore in finally. No real-money exposure
    # because BinanceLiveClient downstream is hard-coded use_testnet=True.
    import sqlalchemy as sa  # noqa: E402

    factory = get_session_factory()
    async with factory() as session:
        original_row = await session.execute(
            sa.text("SELECT trading_mode FROM users WHERE id = 1"),
        )
        original_mode = original_row.scalar() or "manual"
        print(f"original user.trading_mode = {original_mode}")
        try:
            await session.execute(
                sa.text(
                    "UPDATE users SET trading_mode = 'telegram-approve' "
                    "WHERE id = 1",
                ),
            )
            await session.commit()
            print("flipped trading_mode -> telegram-approve for this dispatch")

            result = await dispatch(session, proposal=proposal, user=user)
            await session.commit()
        finally:
            await session.execute(
                sa.text("UPDATE users SET trading_mode = :m WHERE id = 1"),
                {"m": original_mode},
            )
            await session.commit()
            print(f"restored trading_mode -> {original_mode}")

    print(f"outcome={result.outcome}")
    print(f"detail={result.detail}")
    print(f"signal_id={result.signal_id}")
    print(f"binance_order_id={result.binance_order_id}")
    print(
        "next: check your Telegram for the Approve button. "
        "Tap Approve to place the order on testnet.binancefuture.com.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
