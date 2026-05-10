"""SP-9 Phase A — one-off populator for universe_history.

The symbol-search endpoint (GET /api/v1/bot-status/symbol-search)
reads from universe_history. The daily 02:00 UTC cron normally
populates that table, but on a fresh deploy you don't want to wait —
this script fetches every active Binance Futures USDT-margined
perpetual + USDC perpetual + every Bybit Futures USDT-margined
perpetual once and INSERTs them.

Idempotent: re-running just refreshes ``last_synced_at`` on existing
rows. Safe to run as many times as you want.

Run inside the backend container with PYTHONPATH=/app:

    docker compose exec -T backend python /app/tools/populate_universe_history.py

After this, typing "shib" in the dashboard's symbol box should show
SHIB/USDT, 1000SHIB/USDT, etc. immediately.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Path setup so this script can be run from the repo root without
# installing the backend package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.data.adapters import get_adapter, list_registered  # noqa: E402
from app.data.universe_sync import sync_universe  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


async def go() -> None:
    factory = get_session_factory()
    adapters = list_registered()
    if not adapters:
        log.error("no adapters registered; nothing to sync")
        return

    for name in adapters:
        try:
            adapter = get_adapter(name)
        except Exception as e:  # noqa: BLE001
            log.warning("could not resolve adapter %s: %s", name, e)
            continue
        try:
            async with factory() as session:
                result = await sync_universe(adapter, session)
                await session.commit()
            log.info(
                "%s: added=%d still_active=%d newly_delisted=%d",
                name, result.added, result.still_active,
                result.newly_delisted,
            )
        except Exception as e:  # noqa: BLE001
            log.error("%s sync failed: %s", name, e)


if __name__ == "__main__":
    asyncio.run(go())
