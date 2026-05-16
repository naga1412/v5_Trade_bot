"""Safety-net LIVE close-all for BTCUSDT / ETHUSDT on Binance Futures.

Sibling of test_trade_live_round_trip.py. The round-trip script
auto-closes in its finally block, but if that fails for any reason
(network blip, signature error, etc.) the operator has a real-money
position open. This script provides a one-button flatten via the
ops-debug ``test-trade-live-close-all`` probe.

HARD-CODED ``use_testnet=False`` — this only touches LIVE Binance.
ONLY closes positions on the symbol whitelist {BTCUSDT, ETHUSDT}; will
not touch any other position the operator has open by hand.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

for _candidate in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break


ALLOWED_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


async def main() -> int:
    import os
    from app.config import get_settings
    from app.exchanges.binance_live import BinanceLiveClient, BinanceLiveError
    from app.trading.execution.glue import initialize_vault_cache, vault_keys

    settings = get_settings()
    secrets_path = Path(
        os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"),
    )
    if not initialize_vault_cache(
        passphrase=settings.master_passphrase, secrets_path=secrets_path,
    ):
        print("FAIL: vault init", file=sys.stderr)
        return 1
    keys = vault_keys()
    if keys is None:
        print("FAIL: vault_keys()=None", file=sys.stderr)
        return 1

    print("=== LIVE close-all (BTCUSDT, ETHUSDT only) ===")
    client = BinanceLiveClient(
        api_key=keys.binance_api_key,
        api_secret=keys.binance_api_secret,
        use_testnet=False,  # HARD-CODED LIVE
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
    return 0 if closed >= 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
