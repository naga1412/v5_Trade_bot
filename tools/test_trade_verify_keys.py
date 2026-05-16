"""Diagnose Binance API key + IP whitelist + permissions issues.

Triggered by the ops-debug ``test-trade-verify-keys`` probe when the
test-trade-open Approve-tap fails with Binance 401 / -2015.

The probe runs inside the backend container, so it sees the same vault
keys + outbound IP the real dispatcher uses. It then:

  1. Prints the Hetzner container's outbound IP (what Binance's IP
     whitelist must include).
  2. Tries to authenticate the vaulted keys against Binance Futures
     TESTNET (`testnet.binancefuture.com`).
  3. Tries the SAME keys against Binance Futures LIVE
     (`fapi.binance.com`).
  4. Reports OK / 401 / other for each, plus the Binance error code on
     failure so the operator knows exactly what to fix.

Read-only — uses `GET /fapi/v2/positionRisk?symbol=BTCUSDT`, which
requires Futures-Read auth but places no orders.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Path setup — see tools/test_trade_open.py for rationale.
for _candidate in (
    Path(__file__).resolve().parent.parent / "backend",
    Path("/app"),
):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break

import httpx  # noqa: E402


async def _outbound_ip() -> str:
    """Print the IP Binance will see for requests from this container.

    Tries a couple of well-known IP-echo endpoints; falls back to '?'.
    Done with a short timeout so a network blip doesn't gum up the
    rest of the diagnostic.
    """
    async with httpx.AsyncClient(timeout=5.0) as http:
        for url in ("https://ifconfig.me/ip", "https://api.ipify.org"):
            try:
                r = await http.get(url)
                if r.status_code == 200 and r.text.strip():
                    return r.text.strip()
            except httpx.HTTPError:
                continue
    return "?"


async def _probe_env(*, use_testnet: bool, api_key: str, api_secret: str) -> str:
    """Try a signed positionRisk call and return a one-line status."""
    from app.exchanges.binance_live import BinanceLiveClient, BinanceLiveError

    client = BinanceLiveClient(
        api_key=api_key, api_secret=api_secret, use_testnet=use_testnet,
    )
    label = "TESTNET" if use_testnet else "LIVE"
    try:
        # get_position returns None when flat; either way, a successful
        # 2xx means auth + permissions are good for Futures-Read on
        # this environment.
        await client.get_position(symbol="BTCUSDT")
        return f"{label}: ✓ auth OK, key has Futures-Read permission"
    except BinanceLiveError as e:
        msg = str(e)
        if "-2015" in msg:
            return (
                f"{label}: ✗ Binance code -2015 — Invalid API-key, IP, or "
                f"Futures permissions. ({msg[:120]})"
            )
        if "401" in msg or "403" in msg:
            return f"{label}: ✗ Auth rejected ({msg[:120]})"
        return f"{label}: ✗ Error ({msg[:120]})"
    except Exception as e:  # noqa: BLE001
        return f"{label}: ✗ Unexpected error: {type(e).__name__}: {e}"
    finally:
        await client.aclose()


async def main() -> int:
    import os
    from app.config import get_settings
    from app.trading.execution.glue import initialize_vault_cache, vault_keys

    settings = get_settings()
    secrets_path = Path(
        os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"),
    )
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

    print("=== Vault state ===")
    print(f"binance_api_key  (len): {len(keys.binance_api_key)}")
    print(f"binance_api_secret(len): {len(keys.binance_api_secret)}")
    print(f"key prefix:              {keys.binance_api_key[:6]}…")
    print(f"settings.binance_use_testnet: {settings.binance_use_testnet}")

    print("\n=== Hetzner outbound IP (what Binance sees) ===")
    ip = await _outbound_ip()
    print(f"outbound_ip = {ip}")
    print(
        "→ If your Binance API key has an IP whitelist, the above IP "
        "MUST be in it.",
    )

    print("\n=== Auth probe (read-only — no orders placed) ===")
    testnet_status = await _probe_env(
        use_testnet=True,
        api_key=keys.binance_api_key,
        api_secret=keys.binance_api_secret,
    )
    print(testnet_status)
    live_status = await _probe_env(
        use_testnet=False,
        api_key=keys.binance_api_key,
        api_secret=keys.binance_api_secret,
    )
    print(live_status)

    print("\n=== Interpretation ===")
    testnet_ok = testnet_status.startswith("TESTNET: ✓")
    live_ok = live_status.startswith("LIVE: ✓")
    if testnet_ok and live_ok:
        print(
            "Both environments authenticate — key is universal (rare). "
            "The test-trade Approve flow should work; if it still 401s, "
            "the issue is at the set_leverage call (Futures-Trade vs "
            "Futures-Read permission split).",
        )
    elif testnet_ok and not live_ok:
        print(
            "Key is a TESTNET key. The poller is routing to "
            f"{'TESTNET' if settings.binance_use_testnet else 'LIVE'} based "
            "on BINANCE_USE_TESTNET. If that says LIVE, FIX: "
            "set BINANCE_USE_TESTNET=true in .env and restart backend.",
        )
    elif live_ok and not testnet_ok:
        print(
            "Key is a LIVE key. The poller is routing to "
            f"{'TESTNET' if settings.binance_use_testnet else 'LIVE'}. "
            "If you wanted testnet validation, generate a TESTNET key "
            "at testnet.binancefuture.com first. Real-money trade is "
            "possible if you proceed with this key.",
        )
    else:
        print(
            "Neither environment authenticates. Likely causes:\n"
            "  1. Key has Spot-only permissions (need Futures enabled)\n"
            f"  2. Key has IP whitelist that doesn't include {ip}\n"
            "  3. Key was revoked or never existed\n"
            "Fix at the Binance website: API Management → edit the key.",
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
