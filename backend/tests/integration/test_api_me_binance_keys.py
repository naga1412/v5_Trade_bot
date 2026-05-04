"""Integration tests for POST /api/v1/me/binance-keys (Phase H2)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import User
from app.auth.secrets import get_binance_keys
from app.config import get_settings


@pytest.mark.asyncio
async def test_post_me_binance_keys_round_trip(
    friend_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    body = {"api_key": "BNC-API-KEY-123", "api_secret": "BNC-SECRET-456"}
    r = await friend_client.post("/api/v1/me/binance-keys", json=body)
    assert r.status_code == 204, r.text

    async with auth_factory() as s:
        u = await s.get(User, 2)
        assert u is not None
        assert u.binance_api_key_encrypted is not None
        assert u.binance_api_secret_encrypted is not None
        # ciphertext != plaintext
        assert u.binance_api_key_encrypted != "BNC-API-KEY-123"
        # round-trip via helper
        keys = get_binance_keys(u, passphrase=get_settings().master_passphrase)
    assert keys.api_key == "BNC-API-KEY-123"
    assert keys.api_secret == "BNC-SECRET-456"


@pytest.mark.asyncio
async def test_post_me_binance_keys_rejected_during_impersonation(
    auth_factory: async_sessionmaker,
) -> None:
    """If admin is impersonating, /me mutations return 403."""
    from collections.abc import AsyncIterator

    from app.auth.deps import current_user_or_impersonated, require_user
    from app.auth.impersonation import set_active_target
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    # Pre-set an impersonation state row: admin id=1 -> target id=2.
    async with auth_factory() as s:
        await set_active_target(s, admin_id=1, target_id=2)
        await s.commit()

    actual_admin = User(
        id=1, email="admin@x.com", display_name="Admin",
        is_admin=True, is_active=True,
        trading_mode="manual", position_sizing_mode="fixed",
        quiet_hours_enabled=True,
    )
    target = User(
        id=2, email="friend@x.com", display_name="Friend",
        is_admin=False, is_active=True,
        trading_mode="manual", position_sizing_mode="fixed",
        quiet_hours_enabled=True,
    )

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email="admin@x.com", sub="admin", raw={})

    async def _u_actual() -> User:
        return actual_admin

    async def _u_effective() -> User:
        return target

    async def _gen_session() -> AsyncIterator:
        async with auth_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen_session
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u_actual
    app.dependency_overrides[current_user_or_impersonated] = _u_effective
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/v1/me/binance-keys",
                json={"api_key": "k", "api_secret": "s"},
            )
        assert r.status_code == 403
    finally:
        for d in (
            get_session, require_cf_user, require_user,
            current_user_or_impersonated,
        ):
            app.dependency_overrides.pop(d, None)
