"""Integration tests for /api/v1/admin/traps (SP-5 Phase F2).

Mirrors the SP-2 admin-patterns suite: shared ``admin_client`` and
``friend_client`` fixtures, in-memory SQLite engine extended with the
``trap_enabled`` table. Backend-only — frontend page is deferred to SP-6.
"""
from __future__ import annotations

import pytest

# 12 main + 5 short-only traps = 17 total — assertion locked in
# tests/unit/test_traps_base.py + the test_trap_volatility_regime_change suite.
EXPECTED_TRAP_COUNT: int = 17


@pytest.mark.asyncio
async def test_list_traps_returns_all_seventeen_with_default_enabled(  # type: ignore[no-untyped-def]
    admin_client,
) -> None:
    r = await admin_client.get("/api/v1/admin/traps")
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == EXPECTED_TRAP_COUNT
    # No rows in trap_enabled yet → every entry is enabled by default.
    assert all(item["enabled"] is True for item in items)
    # Each entry has the required keys (mirrors PatternEntryOut shape + side).
    sample = items[0]
    assert {"trap_id", "severity", "side", "enabled"} <= set(sample.keys())
    assert sample["severity"] in {"medium", "high", "extreme"}
    assert sample["side"] in {"long", "short", "both"}


@pytest.mark.asyncio
async def test_disable_then_list_shows_disabled(admin_client) -> None:  # type: ignore[no-untyped-def]
    r_dis = await admin_client.post(
        "/api/v1/admin/traps/parabolic_blowoff/disable",
        json={"reason": "too noisy on alts during euphoria"},
    )
    assert r_dis.status_code == 200, r_dis.text
    body = r_dis.json()
    assert body["enabled"] is False
    assert body["disabled_reason"] == "too noisy on alts during euphoria"

    r_list = await admin_client.get("/api/v1/admin/traps")
    assert r_list.status_code == 200
    items_by_id = {i["trap_id"]: i for i in r_list.json()}
    assert "parabolic_blowoff" in items_by_id
    assert items_by_id["parabolic_blowoff"]["enabled"] is False
    assert (
        items_by_id["parabolic_blowoff"]["disabled_reason"]
        == "too noisy on alts during euphoria"
    )


@pytest.mark.asyncio
async def test_enable_after_disable_restores_enabled(admin_client) -> None:  # type: ignore[no-untyped-def]
    await admin_client.post(
        "/api/v1/admin/traps/friday_weekend/disable", json={},
    )
    r = await admin_client.post("/api/v1/admin/traps/friday_weekend/enable")
    assert r.status_code == 200, r.text

    r_list = await admin_client.get("/api/v1/admin/traps")
    items_by_id = {i["trap_id"]: i for i in r_list.json()}
    assert items_by_id["friday_weekend"]["enabled"] is True


@pytest.mark.asyncio
async def test_disable_with_symbol_and_timeframe_scopes_correctly(  # type: ignore[no-untyped-def]
    admin_client,
) -> None:
    """Symbol + timeframe-scoped disable rows coexist with the global default."""
    r = await admin_client.post(
        "/api/v1/admin/traps/counter_weekly/disable",
        json={"symbol": "BTCUSDT", "timeframe": "1h", "reason": "BTC 1h only"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["trap_id"] == "counter_weekly"
    assert body["symbol"] == "BTCUSDT"
    assert body["timeframe"] == "1h"
    assert body["enabled"] is False


@pytest.mark.asyncio
async def test_disable_unknown_trap_id_returns_404(admin_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin_client.post(
        "/api/v1/admin/traps/never_will_exist/disable", json={},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_enable_unknown_trap_id_returns_404(admin_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin_client.post(
        "/api/v1/admin/traps/never_will_exist/enable",
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_enable_when_no_disabled_row_is_idempotent(  # type: ignore[no-untyped-def]
    admin_client,
) -> None:
    """Calling /enable on a trap that was never disabled is a no-op success."""
    r = await admin_client.post("/api/v1/admin/traps/liquidity_sweep/enable")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_disable_then_disable_again_updates_reason_idempotently(  # type: ignore[no-untyped-def]
    admin_client,
) -> None:
    await admin_client.post(
        "/api/v1/admin/traps/parabolic_blowoff/disable", json={"reason": "v1"},
    )
    r2 = await admin_client.post(
        "/api/v1/admin/traps/parabolic_blowoff/disable", json={"reason": "v2"},
    )
    assert r2.status_code == 200
    assert r2.json()["disabled_reason"] == "v2"


@pytest.mark.asyncio
async def test_short_only_traps_show_side_short(admin_client) -> None:  # type: ignore[no-untyped-def]
    """The five short-only traps reach the list with side=='short'."""
    r = await admin_client.get("/api/v1/admin/traps")
    items_by_id = {i["trap_id"]: i for i in r.json()}
    for trap_id in (
        "short_squeeze_cascade",
        "funding_rate_decay",
        "borrow_rate",
        "unlimited_upside_risk",
        "regulatory_short_ban",
    ):
        assert trap_id in items_by_id, f"missing short-only trap {trap_id}"
        assert items_by_id[trap_id]["side"] == "short"


@pytest.mark.asyncio
async def test_non_admin_rejected(friend_client) -> None:  # type: ignore[no-untyped-def]
    """Friend (non-admin) gets 403 from every trap admin endpoint."""
    r = await friend_client.get("/api/v1/admin/traps")
    assert r.status_code == 403
    r = await friend_client.post(
        "/api/v1/admin/traps/parabolic_blowoff/disable", json={},
    )
    assert r.status_code == 403
    r = await friend_client.post("/api/v1/admin/traps/parabolic_blowoff/enable")
    assert r.status_code == 403
