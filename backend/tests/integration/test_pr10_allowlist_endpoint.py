"""PR10 /bot-status/symbol-allowlist endpoint."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa


_NOW = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_symbol_allowlist_endpoint_empty(
    bot_status_client: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/symbol-allowlist")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_symbol_allowlist_endpoint_returns_sorted_by_sharpe_desc(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    async with bot_status_factory() as s:
        for sym, sharpe in (("TONUSDT", 5.84), ("FDUSDUSDT", -18.72), ("XRPUSDT", 2.93)):
            await s.execute(sa.text(
                "INSERT INTO symbol_performance_snapshots "
                "(symbol, window_start, window_end, trades_count, "
                " win_rate, sharpe, allowed, computed_at, prev_hash, row_hash) "
                "VALUES (:sym, :ws, :we, 100, 0.5, :sh, :al, :now, '0', :rh)"
            ), {
                "sym": sym, "ws": _NOW.isoformat(), "we": _NOW.isoformat(),
                "sh": sharpe, "al": sharpe > 0, "now": _NOW.isoformat(),
                "rh": f"hash_{sym}",
            })
        await s.commit()

    r = await bot_status_client.get("/api/v1/bot-status/symbol-allowlist")
    body = r.json()
    assert len(body) == 3
    symbols_ordered = [row["symbol"] for row in body]
    assert symbols_ordered == ["TONUSDT", "XRPUSDT", "FDUSDUSDT"]
