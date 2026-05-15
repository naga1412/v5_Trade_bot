"""Server-side flatten of every open Binance Futures testnet position.

Equivalent of POST /api/v1/admin/test-trade/close-all, but invoked from
INSIDE the backend container by the ops-debug ``test-trade-close-all``
probe. Bypasses Cloudflare Access auth.

Iterates the symbol whitelist (BTCUSDT, ETHUSDT); calls
``BinanceLiveClient(use_testnet=True).close_position()`` for any symbol
with a non-zero position. ``use_testnet=True`` is a literal, NOT
env-driven — this script physically cannot reach live Binance.

Pair with ``test_trade_open.py`` (open) for the round-trip smoke.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Path setup mirrors tools/populate_universe_history.py — needed because
# the file is bind-mounted at /app/host-tools/ in the container while the
# actual `app/` package lives at /app/app/.
for _candidate in (
    Path(__file__).resolve().parent.parent / "backend",  # repo layout
    Path("/app"),                                         # container layout
):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break


ALLOWED_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


async def main() -> int:
    from app.exchanges.binance_live import BinanceLiveClient, BinanceLiveError
    from app.trading.execution.glue import vault_keys

    keys = vault_keys()
    if keys is None:
        print(
            "FAIL: vault not loaded — AUTONOMOUS_TRADING_ENABLED must be true "
            "and preflight must have passed at startup.",
            file=sys.stderr,
        )
        return 1

    client = BinanceLiveClient(
        api_key=keys.binance_api_key,
        api_secret=keys.binance_api_secret,
        use_testnet=True,  # HARD-CODED — DO NOT EDIT
    )
    closed = 0
    skipped = 0
    try:
        for symbol in ALLOWED_SYMBOLS:
            try:
                pos = await client.get_position(symbol=symbol)
            except BinanceLiveError as e:
                print(f"{symbol}: get_position FAILED — {e}")
                skipped += 1
                continue
            if pos is None or pos.position_amt == 0:
                print(f"{symbol}: no open position")
                skipped += 1
                continue
            try:
                r = await client.close_position(symbol=symbol)
                print(
                    f"{symbol}: CLOSED qty={r.qty} @ {r.avg_fill_price} "
                    f"order_id={r.binance_order_id}",
                )
                closed += 1
            except BinanceLiveError as e:
                print(f"{symbol}: close_position FAILED — {e}")
                skipped += 1
    finally:
        await client.aclose()

    print(f"summary: closed={closed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
