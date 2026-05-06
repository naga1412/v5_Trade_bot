import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.adapters.binance_futures_intermarket import IntermarketSnapshot
from app.data.intermarket_worker import (
    INTERMARKET_INTERVAL_S,
    run_intermarket_snapshot_loop,
)


def _fake_factory():
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    sf.return_value.__aexit__ = AsyncMock(return_value=None)
    return sf


@pytest.mark.asyncio
async def test_snapshot_loop_polls_universe_then_persists(monkeypatch) -> None:
    sleep_log: list[float] = []
    async def fake_sleep(s: float) -> None:
        sleep_log.append(s)
        if len(sleep_log) >= 1:
            raise asyncio.CancelledError

    snap1 = IntermarketSnapshot(
        symbol="BTC/USDT",
        captured_at=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
        funding_rate=-0.0001, mark_price=70000.0, open_interest=1.0e9,
        source="binance_futures",
    )
    snap2 = IntermarketSnapshot(
        symbol="ETH/USDT",
        captured_at=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
        funding_rate=0.0002, mark_price=3000.0, open_interest=2.0e8,
        source="binance_futures",
    )
    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(side_effect=[snap1, snap2])

    async def universe_loader(session):  # type: ignore[no-untyped-def]
        return ["BTC/USDT", "ETH/USDT"]

    persist_mock = AsyncMock(return_value=2)
    with patch("app.data.intermarket_worker.persist_intermarket_snapshots",
               new=persist_mock):
        with pytest.raises(asyncio.CancelledError):
            await run_intermarket_snapshot_loop(
                _fake_factory(),
                _adapter=adapter,
                _sleep=fake_sleep,
                _universe_loader=universe_loader,
            )
    assert adapter.fetch_snapshot.await_count == 2
    persist_mock.assert_awaited_once()
    persisted = list(persist_mock.await_args.args[1])
    assert {s.symbol for s in persisted} == {"BTC/USDT", "ETH/USDT"}
    assert sleep_log[0] == float(INTERMARKET_INTERVAL_S)


@pytest.mark.asyncio
async def test_snapshot_loop_skips_symbols_returning_none() -> None:
    sleep_log: list[float] = []
    async def fake_sleep(s: float) -> None:
        sleep_log.append(s)
        if len(sleep_log) >= 1:
            raise asyncio.CancelledError

    snap = IntermarketSnapshot(
        symbol="BTC/USDT",
        captured_at=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
        funding_rate=-0.0001, mark_price=70000.0, open_interest=1.0e9,
        source="binance_futures",
    )
    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(side_effect=[snap, None])

    async def universe_loader(session):  # type: ignore[no-untyped-def]
        return ["BTC/USDT", "DOGEFAIL/USDT"]

    persist_mock = AsyncMock(return_value=1)
    with patch("app.data.intermarket_worker.persist_intermarket_snapshots",
               new=persist_mock):
        with pytest.raises(asyncio.CancelledError):
            await run_intermarket_snapshot_loop(
                _fake_factory(), _adapter=adapter,
                _sleep=fake_sleep, _universe_loader=universe_loader,
            )
    persist_mock.assert_awaited_once()
    persisted = list(persist_mock.await_args.args[1])
    assert {s.symbol for s in persisted} == {"BTC/USDT"}


@pytest.mark.asyncio
async def test_snapshot_loop_swallows_iteration_error() -> None:
    sleep_log: list[float] = []
    async def fake_sleep(s: float) -> None:
        sleep_log.append(s)
        if len(sleep_log) >= 2:
            raise asyncio.CancelledError

    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(side_effect=RuntimeError("boom"))

    async def universe_loader(session):  # type: ignore[no-untyped-def]
        return ["BTC/USDT"]

    with pytest.raises(asyncio.CancelledError):
        await run_intermarket_snapshot_loop(
            _fake_factory(), _adapter=adapter,
            _sleep=fake_sleep, _universe_loader=universe_loader,
        )
    # Loop survived past first error.
    assert adapter.fetch_snapshot.await_count == 2
