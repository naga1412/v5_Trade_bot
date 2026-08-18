"""Integration tests for GET /api/v1/bot-status/telegram-signals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa


@pytest.mark.asyncio
async def test_telegram_signals_endpoint_returns_dispatched_rows(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    async with bot_status_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload, response, symbol_source) "
            "VALUES (:id, 1, 'FOO/USDT', 'LONG', :sent_at, :payload, NULL, 'futures_poll')"
        ), {
            "id": "sig-1", "sent_at": datetime.now(timezone.utc).isoformat(),
            "payload": json.dumps({
                "entry_price": 100.0, "stop_loss_price": 95.0, "take_profit_price": 110.0,
                "rr_ratio": 2.0, "confidence_pct": 70.0,
                "qvol_24h": 25_000_000.0, "spread_bps": 2.0, "depth_0_5pct_usdt": 80_000.0,
            }),
        })
        await session.commit()

    resp = await bot_status_client.get("/api/v1/bot-status/telegram-signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "FOO/USDT"
    assert body[0]["symbol_source"] == "futures_poll"
    assert body[0]["status"] is None  # response column was NULL -- pending


@pytest.mark.asyncio
async def test_telegram_signals_filters_by_symbol_source(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    async with bot_status_factory() as session:
        for sym, src in [("FOO/USDT", "futures_poll"), ("BTC/USDT", "established_top20")]:
            await session.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, user_id, symbol, direction, sent_at, payload, symbol_source) "
                "VALUES (:id, 1, :sym, 'LONG', :sent_at, '{}', :src)"
            ), {
                "id": f"sig-{sym}", "sym": sym,
                "sent_at": datetime.now(timezone.utc).isoformat(), "src": src,
            })
        await session.commit()

    resp = await bot_status_client.get(
        "/api/v1/bot-status/telegram-signals?symbol_source=futures_poll"
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "FOO/USDT"


@pytest.mark.asyncio
async def test_telegram_signals_accepts_all_response_check_constraint_values(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """Guards against re-narrowing TelegramSignalOut.status: the DB's
    telegram_signals_response_check constraint (migration
    0027_widen_response_check) allows 7 values, not the 4 the original
    Phase 4 plan doc's Step 4 snippet specified. 'stale_price' (price-
    drift guard), 'auto_skipped' (timeout worker), and 'approve_lost_race'
    (approval-race handler) are all written by real, actively-running
    code -- a row with any of them would 500 this endpoint if the
    Literal ever regresses back to the original 4-value list."""
    values = ("approved", "skipped", "timeout", "error",
              "stale_price", "auto_skipped", "approve_lost_race")
    async with bot_status_factory() as session:
        for i, resp_val in enumerate(values):
            await session.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, user_id, symbol, direction, sent_at, payload, response, symbol_source) "
                "VALUES (:id, 1, :sym, 'LONG', :sent_at, '{}', :resp, 'established_top20')"
            ), {
                "id": f"sig-status-{i}", "sym": f"SYM{i}/USDT",
                "sent_at": datetime.now(timezone.utc).isoformat(), "resp": resp_val,
            })
        await session.commit()

    resp = await bot_status_client.get("/api/v1/bot-status/telegram-signals?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    returned_statuses = {row["status"] for row in body if row["symbol"].startswith("SYM")}
    assert returned_statuses == set(values)
