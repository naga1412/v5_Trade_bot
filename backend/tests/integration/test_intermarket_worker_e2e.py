from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa

from app.data.adapters.binance_futures_intermarket import IntermarketSnapshot
from app.data.intermarket_worker import _snapshot_once


@pytest.mark.integration
@pytest.mark.asyncio
async def test_snapshot_once_persists_30_rows(bot_status_factory) -> None:
    """One tick of the snapshot loop persists exactly N rows."""
    symbols = [f"SYM{i}/USDT" for i in range(30)]

    async def fake_universe_loader(session) -> list[str]:
        return symbols

    def make_snap(sym: str) -> IntermarketSnapshot:
        return IntermarketSnapshot(
            symbol=sym,
            captured_at=datetime.now(timezone.utc),
            funding_rate=-0.0001,
            mark_price=1000.0,
            open_interest=1.0e6,
            source="binance_futures",
        )

    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(side_effect=[make_snap(s) for s in symbols])

    n = await _snapshot_once(bot_status_factory, adapter, fake_universe_loader)
    assert n == 30

    async with bot_status_factory() as session:
        row = (await session.execute(
            sa.text("SELECT COUNT(*) AS n FROM intermarket_snapshots")
        )).first()
    assert row.n == 30
