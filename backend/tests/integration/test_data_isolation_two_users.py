"""Phase L1: end-to-end data isolation between two users.

Spec §7.1 + §9.2 — every per-user table carries `user_id NOT NULL`, and every
read endpoint filters by `current_user_or_impersonated.id`. This test plants
a `shadow_trades` row owned by user 2 and asserts that an authenticated client
bound to user 1 sees an empty result set; the symmetric direction confirms
the row is not silently dropped on the way in.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.audit import insert_with_chain


def _trade_payload(
    *, user_id: int, signal_id: str, closed_at: datetime,
) -> dict[str, Any]:
    opened_at = closed_at - timedelta(hours=4)
    entry, exit_price = 100.0, 110.0
    pnl_pct = (exit_price - entry) / entry * 100.0
    return {
        "user_id": user_id,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "direction": "LONG",
        "entry_price": entry,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "position_size_usdt": 30.0,
        "entry_score": 0.6,
        "entry_confidence": 0.7,
        "layer_scores": json.dumps({}),
        "entry_atr": 1.0,
        "exit_price": exit_price,
        "exit_reason": "TAKE_PROFIT",
        "pnl_pct": pnl_pct,
        "pnl_usdt": 30.0 * pnl_pct / 100.0,
        "bars_held": 4,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "inputs_hash": "h" * 64,
        "model_version": "sp-0.7",
        "signal_id": signal_id,
    }


async def _seed_user_b(factory: async_sessionmaker[AsyncSession]) -> None:
    """Insert user_id=2 plus one chain-compliant shadow_trades row owned by 2."""
    async with factory() as session:
        await session.execute(sa.text(
            "INSERT INTO users (id, email, display_name, is_admin) "
            "VALUES (2, 'b@local', 'B', 0)"
        ))
        await session.commit()
        await insert_with_chain(
            session,
            "shadow_trades",
            _trade_payload(
                user_id=2,
                signal_id="user-b-trade-1",
                closed_at=datetime.now(UTC) - timedelta(hours=1),
            ),
        )
        await session.commit()


def _client_for_user(
    factory: async_sessionmaker[AsyncSession],
    *, user_id: int, email: str, is_admin: bool,
) -> Any:
    """Build an httpx.AsyncClient whose deps resolve to the given user."""
    from app.auth.deps import current_user_or_impersonated, require_user
    from app.auth.models import User
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    user = User(
        id=user_id, email=email, display_name=email.split("@")[0],
        is_admin=is_admin, is_active=True,
    )

    async def _gen_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email=email, sub=email, raw={})

    async def _u() -> User:
        return user

    app.dependency_overrides[get_session] = _gen_session
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[current_user_or_impersonated] = _u

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _clear_overrides() -> None:
    from app.auth.deps import current_user_or_impersonated, require_user
    from app.db.session import get_session
    from app.deps import require_cf_user
    from app.main import app

    for d in (
        get_session, require_cf_user, require_user, current_user_or_impersonated,
    ):
        app.dependency_overrides.pop(d, None)


@pytest_asyncio.fixture
async def user_a_client(
    bot_status_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    client = _client_for_user(
        bot_status_factory, user_id=1, email="a@local", is_admin=True,
    )
    try:
        async with client as c:
            yield c
    finally:
        _clear_overrides()


@pytest_asyncio.fixture
async def user_b_client(
    bot_status_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    client = _client_for_user(
        bot_status_factory, user_id=2, email="b@local", is_admin=False,
    )
    try:
        async with client as c:
            yield c
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_user_a_cannot_see_user_b_trades(
    bot_status_factory: async_sessionmaker[AsyncSession],
    user_a_client: httpx.AsyncClient,
) -> None:
    """User A's /recent-trades is empty even though user B has a trade row."""
    await _seed_user_b(bot_status_factory)

    r = await user_a_client.get("/api/v1/bot-status/recent-trades")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == [], (
        f"expected empty list — user A leaked user B's trades: {body!r}"
    )


@pytest.mark.asyncio
async def test_user_b_sees_own_trade(
    bot_status_factory: async_sessionmaker[AsyncSession],
    user_b_client: httpx.AsyncClient,
) -> None:
    """Symmetric check: the row exists and user B sees exactly that one row."""
    await _seed_user_b(bot_status_factory)

    r = await user_b_client.get("/api/v1/bot-status/recent-trades")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["signal_id"] == "user-b-trade-1"
    assert body[0]["symbol"] == "BTC/USDT"
