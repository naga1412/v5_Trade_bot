"""Integration tests for /api/v1/admin/patterns (SP-2 Phase E task E5).

Mirrors the SP-1 admin-ml-checkpoints pattern: shared ``admin_client`` and
``friend_client`` fixtures, in-memory SQLite engine extended with the
``pattern_enabled`` table. Backend-only — frontend page is deferred to SP-6.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_patterns_returns_all_158_with_default_enabled(admin_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin_client.get("/api/v1/admin/patterns")
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 158
    # No rows in pattern_enabled yet → every entry is enabled by default.
    assert all(item["enabled"] is True for item in items)
    # Each entry has required keys.
    sample = items[0]
    assert {"pattern_id", "pattern_type", "enabled"} <= set(sample.keys())


@pytest.mark.asyncio
async def test_disable_then_list_shows_disabled(admin_client) -> None:  # type: ignore[no-untyped-def]
    r_dis = await admin_client.post(
        "/api/v1/admin/patterns/hammer/disable",
        json={"reason": "too noisy on BTC 1h"},
    )
    assert r_dis.status_code == 200, r_dis.text

    r_list = await admin_client.get("/api/v1/admin/patterns")
    assert r_list.status_code == 200
    items_by_id = {i["pattern_id"]: i for i in r_list.json()}
    assert "hammer" in items_by_id
    assert items_by_id["hammer"]["enabled"] is False
    assert items_by_id["hammer"]["disabled_reason"] == "too noisy on BTC 1h"


@pytest.mark.asyncio
async def test_enable_after_disable_restores_enabled(admin_client) -> None:  # type: ignore[no-untyped-def]
    await admin_client.post("/api/v1/admin/patterns/doji/disable", json={})
    r = await admin_client.post("/api/v1/admin/patterns/doji/enable")
    assert r.status_code == 200, r.text

    r_list = await admin_client.get("/api/v1/admin/patterns")
    items_by_id = {i["pattern_id"]: i for i in r_list.json()}
    assert items_by_id["doji"]["enabled"] is True


@pytest.mark.asyncio
async def test_disable_with_symbol_and_timeframe_scopes_correctly(admin_client) -> None:  # type: ignore[no-untyped-def]
    """Symbol + timeframe-scoped disable rows coexist with the global default."""
    r = await admin_client.post(
        "/api/v1/admin/patterns/engulfing/disable",
        json={"symbol": "BTCUSDT", "timeframe": "1h", "reason": "BTC 1h only"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pattern_id"] == "engulfing"
    assert body["symbol"] == "BTCUSDT"
    assert body["timeframe"] == "1h"
    assert body["enabled"] is False


@pytest.mark.asyncio
async def test_disable_unknown_pattern_id_returns_404(admin_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin_client.post(
        "/api/v1/admin/patterns/never_will_exist/disable", json={},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_enable_when_no_disabled_row_is_idempotent(admin_client) -> None:  # type: ignore[no-untyped-def]
    """Calling /enable on a pattern that was never disabled is a no-op success."""
    r = await admin_client.post("/api/v1/admin/patterns/doji/enable")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_disable_then_disable_again_updates_reason_idempotently(  # type: ignore[no-untyped-def]
    admin_client,
) -> None:
    await admin_client.post(
        "/api/v1/admin/patterns/hammer/disable", json={"reason": "v1"},
    )
    r2 = await admin_client.post(
        "/api/v1/admin/patterns/hammer/disable", json={"reason": "v2"},
    )
    assert r2.status_code == 200
    assert r2.json()["disabled_reason"] == "v2"


@pytest.mark.asyncio
async def test_non_admin_rejected(friend_client) -> None:  # type: ignore[no-untyped-def]
    """Friend (non-admin) gets 403 from every pattern admin endpoint."""
    r = await friend_client.get("/api/v1/admin/patterns")
    assert r.status_code == 403
    r = await friend_client.post(
        "/api/v1/admin/patterns/hammer/disable", json={},
    )
    assert r.status_code == 403
    r = await friend_client.post("/api/v1/admin/patterns/hammer/enable")
    assert r.status_code == 403
