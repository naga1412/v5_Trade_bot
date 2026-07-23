"""PR10 symbol_allowlist_refresh worker — writes 1 snapshot per symbol."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workers.symbol_allowlist_refresh import (
    run_one_refresh_cycle,
    run_symbol_allowlist_refresh_loop,
)


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


async def _mk_engine_with_shadow_trades():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "direction TEXT NOT NULL, "
            "closed_at TEXT, "
            "pnl_usdt REAL NOT NULL DEFAULT 0, "
            "pnl_pct REAL NOT NULL DEFAULT 0, "
            "prev_hash TEXT NOT NULL DEFAULT '', "
            "row_hash TEXT NOT NULL DEFAULT '')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE symbol_performance_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "window_start TEXT NOT NULL, "
            "window_end TEXT NOT NULL, "
            "trades_count INTEGER NOT NULL, "
            "win_rate REAL, sharpe REAL, "
            "allowed INTEGER NOT NULL, "
            "computed_at TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, "
            "row_hash TEXT NOT NULL UNIQUE, "
            "inputs_hash TEXT)"
        ))
        for sym, pnl in (("BTCUSDT", 1.0), ("BTCUSDT", -0.5), ("ETHUSDT", 2.0)):
            await conn.execute(sa.text(
                "INSERT INTO shadow_trades "
                "(user_id, symbol, direction, closed_at, pnl_usdt, pnl_pct) "
                "VALUES (1, :sym, 'LONG', :ts, :pnl, :pct)"
            ), {"sym": sym, "ts": _NOW.isoformat(), "pnl": pnl, "pct": pnl * 0.01})
    return engine


def _settings():
    return SimpleNamespace(
        SYMBOL_ALLOWLIST_WINDOW_TRADES=100,
        SYMBOL_ALLOWLIST_WINDOW_DAYS=30,
        SYMBOL_ALLOWLIST_GRACE_TRADES=50,
    )


@pytest.mark.asyncio
async def test_refresh_cycle_writes_one_row_per_symbol() -> None:
    engine = await _mk_engine_with_shadow_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    with patch("app.workers.symbol_allowlist_refresh.record_heartbeat",
               new=AsyncMock(return_value=None)):
        await run_one_refresh_cycle(
            session_factory=factory, settings=_settings(), now_fn=lambda: _NOW,
        )

    async with factory() as s:
        rows = (await s.execute(sa.text(
            "SELECT symbol, trades_count FROM symbol_performance_snapshots "
            "ORDER BY symbol"
        ))).all()
    symbols = {r.symbol for r in rows}
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols


@pytest.mark.asyncio
async def test_loop_first_cycle_completes_with_ok_heartbeat() -> None:
    """Stronger than merely checking the cycle fires: assert it COMPLETES
    successfully and heartbeats status='ok'.

    Regression history:
      - 2026-07-22: sleep-first loop meant the first cycle never fired
        (fixed in PR #344).
      - 2026-07-23: PR #344 exposed a dormant .isoformat() SQL bind bug
        against asyncpg TIMESTAMPTZ — the cycle fired but instantly
        errored, heartbeats stuck at status='error' for ~2 months
        undetected. This assertion (status='ok', not just 'fired') is
        the one that would have caught that class two months ago.

    Runs the REAL run_one_refresh_cycle against an in-memory SQLite —
    the loop-shape + cycle body are both exercised. Only record_heartbeat
    is mocked (its INSERT uses Postgres JSONB cast + NOW() which SQLite
    doesn't speak).
    """
    engine = await _mk_engine_with_shadow_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    call_order: list[str] = []

    async def fake_sleep(_: float) -> None:
        call_order.append("sleep")
        raise asyncio.CancelledError

    hb_mock = AsyncMock(return_value=None)

    with patch(
        "app.workers.symbol_allowlist_refresh.record_heartbeat", new=hb_mock,
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_symbol_allowlist_refresh_loop(
                session_factory=factory,
                settings_factory=_settings,
                poll_interval_s=86400.0,
                _sleep=fake_sleep,
            )

    # Cycle-before-sleep ordering (PR #344 assertion).
    assert call_order == ["sleep"], (
        f"expected first sleep only AFTER cycle completed, got {call_order!r}"
    )
    # Cycle-body-succeeded assertion (PR #350 assertion — the one that
    # would have caught the .isoformat() bug on the first commit).
    hb_mock.assert_awaited()
    call_kwargs = hb_mock.call_args.kwargs
    assert call_kwargs.get("status") == "ok", (
        f"first cycle should heartbeat status='ok'; got "
        f"{call_kwargs.get('status')!r} details={call_kwargs.get('details')!r}"
    )


@pytest.mark.asyncio
async def test_refresh_cycle_heartbeats() -> None:
    engine = await _mk_engine_with_shadow_trades()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hb_mock = AsyncMock(return_value=None)

    with patch("app.workers.symbol_allowlist_refresh.record_heartbeat", new=hb_mock):
        await run_one_refresh_cycle(
            session_factory=factory, settings=_settings(), now_fn=lambda: _NOW,
        )

    hb_mock.assert_awaited()
    args, kwargs = hb_mock.call_args
    assert args[1] == "symbol_allowlist_refresh"
    assert kwargs.get("status") == "ok"
