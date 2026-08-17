from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only. "
           "Set DATABASE_URL=postgresql+asyncpg://... to run locally.",
)


@pytest.mark.asyncio
async def test_live_fleet_universe_table_shape() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('FOOUSDT', 'liquidity_added_spot', 25000000, 2.5, 60000, now())"
        ))
        row = (await conn.execute(sa.text(
            "SELECT cohort FROM live_fleet_universe WHERE symbol = 'FOOUSDT'"
        ))).one()
        assert row.cohort == "liquidity_added_spot"
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_fleet_universe_pk_allows_same_symbol_across_snapshots() -> None:
    """PK is (symbol, snapshot_at), not symbol alone -- a symbol must be
    insertable again on the next day's snapshot without violating a
    uniqueness constraint (mirrors asset_universe's own snapshot-keyed
    shape / UNIQUE (symbol, snapshot_at))."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('BARUSDT', 'futures_poll', 21000000, 4.0, 51000, "
            "'2026-08-16T00:00:00Z')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO live_fleet_universe "
            "(symbol, cohort, qvol_24h, spread_bps, depth_0_5pct_usdt, snapshot_at) "
            "VALUES ('BARUSDT', 'futures_poll', 22000000, 3.5, 52000, "
            "'2026-08-17T00:00:00Z')"
        ))
        count = (await conn.execute(sa.text(
            "SELECT COUNT(*) AS n FROM live_fleet_universe WHERE symbol = 'BARUSDT'"
        ))).one()
        assert count.n == 2
    await engine.dispose()
