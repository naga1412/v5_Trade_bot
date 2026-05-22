"""PR-DIAG-AUTONOMOUS-WORKERS — investigate why the 3 gated workers are dead.

Read-only. Runs preflight in-process and prints which checks pass/fail.
"""
import sys
sys.path.insert(0, "/app")

import asyncio
import os

from app.config import get_settings
from app.db.session import get_session_factory
from app.trading.preflight import run_preflight


async def main() -> None:
    s = get_settings()

    print("=== Step 4a: gate-chain values at runtime ===")
    print(f"settings.autonomous_trading_enabled = {s.autonomous_trading_enabled}")
    print(f"settings.binance_use_testnet        = {s.binance_use_testnet}")
    print(f"settings.master_passphrase set      = {bool(s.master_passphrase) and s.master_passphrase != 'test-passphrase-only-for-tests-do-not-use-in-prod'}")
    print(f"TELEGRAM_BOT_TOKEN in env           = {bool(os.environ.get('TELEGRAM_BOT_TOKEN', '').strip())}")
    print(f"TELEGRAM_CHAT_ID in env             = {bool(os.environ.get('TELEGRAM_CHAT_ID', '').strip())}")

    secrets_path = os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc")
    print(f"VAULT_SECRETS_PATH                  = {secrets_path}")
    print(f"  secrets.enc exists at that path   = {os.path.exists(secrets_path)}")

    print("\n=== Step 4b: live preflight call (5 checks) ===")
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            pf = await run_preflight(
                session, use_testnet=s.binance_use_testnet,
            )
        print(f"all_passed = {pf.all_passed}")
        print(f"summary    = {pf.summary_line()}")
        print("per-check:")
        for c in pf.checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"  {status:4s} {c.name:30s} {c.detail}")
    except Exception as e:  # noqa: BLE001
        print(f"preflight raised: {type(e).__name__}: {e}")


asyncio.run(main())
