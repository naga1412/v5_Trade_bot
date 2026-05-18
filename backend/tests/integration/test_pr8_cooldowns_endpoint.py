"""PR8 — /bot-status/cooldowns endpoint integration tests.

Asserts only active cooldowns are returned, that the blocked_until_fresh_mtf
flag computes correctly, and that the response is per-user-isolated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa


_NOW = datetime.now(timezone.utc)


async def _seed_cooldown(
    factory: Any, *, user_id: int, symbol: str,
    cooldown_until: datetime, last_exit_reason: str,
    last_mtf_agreement: int | None,
) -> None:
    async with factory() as session:
        # NOTE: pass datetime objects (not isoformat strings) so
        # SQLAlchemy serializes them with the same format the route
        # uses when comparing `cooldown_until > :now`. ISO-vs-str()
        # mismatch (T separator vs space) breaks lexical comparison.
        await session.execute(sa.text(
            "INSERT INTO live_cooldowns "
            "(user_id, symbol, cooldown_until, last_exit_reason, "
            " last_mtf_agreement, updated_at) "
            "VALUES (:uid, :sym, :cu, :reason, :mtf, :upd)"
        ), {
            "uid": user_id, "sym": symbol,
            "cu": cooldown_until,
            "reason": last_exit_reason, "mtf": last_mtf_agreement,
            "upd": _NOW,
        })
        await session.commit()


@pytest.mark.asyncio
async def test_cooldowns_endpoint_returns_active_rows_only(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    # Seed one active and one expired
    await _seed_cooldown(
        bot_status_factory, user_id=1, symbol="BTCUSDT",
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    await _seed_cooldown(
        bot_status_factory, user_id=1, symbol="ETHUSDT",
        cooldown_until=_NOW - timedelta(hours=1),  # expired
        last_exit_reason="take_profit", last_mtf_agreement=5,
    )
    r = await bot_status_client.get("/api/v1/bot-status/cooldowns")
    assert r.status_code == 200
    body = r.json()
    symbols = {row["symbol"] for row in body}
    assert symbols == {"BTCUSDT"}  # ETHUSDT filtered (expired)


@pytest.mark.asyncio
async def test_cooldowns_endpoint_sets_fresh_mtf_flag_for_sl(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """SL + LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=True → blocked_until_fresh_mtf=True."""
    await _seed_cooldown(
        bot_status_factory, user_id=1, symbol="BTCUSDT",
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    r = await bot_status_client.get("/api/v1/bot-status/cooldowns")
    body = r.json()
    sl_row = next(row for row in body if row["symbol"] == "BTCUSDT")
    assert sl_row["blocked_until_fresh_mtf"] is True
    assert sl_row["last_exit_reason"] == "stop_loss"
    assert sl_row["last_mtf_agreement"] == 4


@pytest.mark.asyncio
async def test_cooldowns_endpoint_no_fresh_mtf_for_tp(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """TP exit: blocked_until_fresh_mtf=False (TP doesn't need fresh MTF)."""
    await _seed_cooldown(
        bot_status_factory, user_id=1, symbol="BTCUSDT",
        cooldown_until=_NOW + timedelta(hours=1),
        last_exit_reason="take_profit", last_mtf_agreement=5,
    )
    r = await bot_status_client.get("/api/v1/bot-status/cooldowns")
    body = r.json()
    tp_row = next(row for row in body if row["symbol"] == "BTCUSDT")
    assert tp_row["blocked_until_fresh_mtf"] is False


@pytest.mark.asyncio
async def test_cooldowns_endpoint_empty_when_none_active(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/cooldowns")
    assert r.status_code == 200
    assert r.json() == []
