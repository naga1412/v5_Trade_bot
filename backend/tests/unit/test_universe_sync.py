"""Unit tests for sync_universe (SP-3 Phase F)."""
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data.adapters._base import Candle, SymbolInfo
from app.data.universe_sync import SyncResult, sync_universe


def _mk_engine_with_universe_history() -> Any:
    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def _create_table(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE universe_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, "
            "symbol TEXT NOT NULL, "
            "asset_class TEXT NOT NULL, "
            "listed_at TIMESTAMP NOT NULL, "
            "delisted_at TIMESTAMP, "
            "last_synced_at TIMESTAMP NOT NULL, "
            "metadata TEXT, "
            "UNIQUE (exchange, symbol))"
        ))


class FakeAdapter:
    name = "binance"

    def __init__(self, symbols: list[SymbolInfo]) -> None:
        self._symbols = symbols

    async def fetch_klines(self, **kwargs: Any) -> list[Candle]:
        return []

    async def list_symbols(self) -> list[SymbolInfo]:
        return self._symbols


def _info(canonical: str, *, asset_class: str = "crypto") -> SymbolInfo:
    return SymbolInfo(
        canonical=canonical,
        native=canonical.replace("/", ""),
        base=canonical.split("/")[0],
        quote=canonical.split("/")[-1] if "/" in canonical else "",
        listed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        delisted_at=None,
        asset_class=asset_class,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_first_sync_inserts_all_symbols() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    adapter = FakeAdapter([_info("BTC/USDT"), _info("ETH/USDT")])
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session)
        await session.commit()
    assert isinstance(result, SyncResult)
    assert result.added == 2
    assert result.still_active == 0
    assert result.newly_delisted == 0


@pytest.mark.asyncio
async def test_second_sync_no_changes_yields_still_active() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    adapter = FakeAdapter([_info("BTC/USDT")])
    async with AsyncSession(engine) as session:
        await sync_universe(adapter, session)
        await session.commit()
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session)
        await session.commit()
    assert result.added == 0
    assert result.still_active == 1
    assert result.newly_delisted == 0


@pytest.mark.asyncio
async def test_symbol_disappears_from_api_marks_delisted() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    full = FakeAdapter([_info("BTC/USDT"), _info("LUNA/USDT")])
    async with AsyncSession(engine) as session:
        await sync_universe(full, session)
        await session.commit()

    luna_gone = FakeAdapter([_info("BTC/USDT")])
    async with AsyncSession(engine) as session:
        result = await sync_universe(luna_gone, session)
        await session.commit()

    assert result.newly_delisted == 1

    async with AsyncSession(engine) as session:
        row = (await session.execute(
            sa.text(
                "SELECT delisted_at FROM universe_history "
                "WHERE symbol='LUNA/USDT'"
            )
        )).first()
    assert row is not None
    assert row.delisted_at is not None


@pytest.mark.asyncio
async def test_already_delisted_symbol_stays_delisted() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    async with AsyncSession(engine) as session:
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, delisted_at, "
            "last_synced_at) "
            "VALUES ('binance', 'OLD/USDT', 'crypto', :l, :d, :s)"
        ), {
            "l": datetime(2018, 1, 1, tzinfo=timezone.utc),
            "d": datetime(2022, 6, 1, tzinfo=timezone.utc),
            "s": datetime(2022, 6, 1, tzinfo=timezone.utc),
        })
        await session.commit()
    adapter = FakeAdapter([])
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session, skip_if_empty=False)
        await session.commit()
    assert result.newly_delisted == 0


@pytest.mark.asyncio
async def test_relisting_resets_delisted_at_to_null() -> None:
    """If a symbol comes back, sync clears delisted_at instead of duplicating."""
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    async with AsyncSession(engine) as session:
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, delisted_at, "
            "last_synced_at) "
            "VALUES ('binance', 'COME/BACK', 'crypto', :l, :d, :s)"
        ), {
            "l": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "d": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "s": datetime(2024, 1, 1, tzinfo=timezone.utc),
        })
        await session.commit()
    adapter = FakeAdapter([_info("COME/BACK")])
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session)
        await session.commit()
    assert result.added == 0
    assert result.still_active == 1

    async with AsyncSession(engine) as session:
        row = (await session.execute(sa.text(
            "SELECT delisted_at FROM universe_history "
            "WHERE symbol='COME/BACK'"
        ))).first()
    assert row is not None
    assert row.delisted_at is None


@pytest.mark.asyncio
async def test_empty_list_symbols_short_circuits_to_zero_result() -> None:
    """Yahoo / TwelveData adapters return [] from list_symbols — sync no-op."""
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    adapter = FakeAdapter([])
    async with AsyncSession(engine) as session:
        # Pre-seed with a manual entry to verify it's not flipped to delisted.
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at) "
            "VALUES ('binance', 'MANUAL/SEED', 'crypto', :l, :l)"
        ), {"l": datetime(2026, 5, 1, tzinfo=timezone.utc)})
        await session.commit()

    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session, skip_if_empty=True)
        await session.commit()
    assert result == SyncResult(added=0, still_active=0, newly_delisted=0)

    async with AsyncSession(engine) as session:
        row = (await session.execute(sa.text(
            "SELECT delisted_at FROM universe_history "
            "WHERE symbol='MANUAL/SEED'"
        ))).first()
    assert row is not None
    assert row.delisted_at is None
