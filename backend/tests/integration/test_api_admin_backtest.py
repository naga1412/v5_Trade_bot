"""Integration tests for /api/v1/admin/backtests REST endpoints (SP-7 Phase B5).

Mirrors the SP-1 admin-routes test pattern: uses the shared ``admin_client``
fixture (acting as user_id=1, admin) plus ``friend_client`` (non-admin) for
the 403 case. The ``backtests`` table is created by the auth conftest.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest


@pytest.mark.asyncio
async def test_post_backtests_admin_only(friend_client) -> None:  # type: ignore[no-untyped-def]
    """Non-admin user gets 403 from POST."""
    resp = await friend_client.post("/api/v1/admin/backtests", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_backtests_admin_only(friend_client) -> None:  # type: ignore[no-untyped-def]
    """Non-admin user gets 403 from GET."""
    resp = await friend_client.get("/api/v1/admin/backtests")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_post_backtests_synthetic_runs(  # type: ignore[no-untyped-def]
    admin_client, monkeypatch,
) -> None:
    """Admin can POST; we patch the bars loader to inject synthetic data."""
    import tools.backtest as bt

    def _fake_loader(symbol, tf, start, end):  # type: ignore[no-untyped-def]
        n = 300
        idx = pd.DatetimeIndex(
            [start + timedelta(hours=i) for i in range(n)],
        )
        c = np.array([100 + np.sin(i * 0.1) * 5 + i * 0.05 for i in range(n)])
        return pd.DataFrame({
            "open": c - 0.1, "high": c + 0.5, "low": c - 0.5,
            "close": c, "volume": np.full(n, 1000.0),
        }, index=idx)

    monkeypatch.setattr(bt, "_default_bars_loader", _fake_loader)

    resp = await admin_client.post("/api/v1/admin/backtests", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "start": "2025-01-01T00:00:00Z", "end": "2025-01-15T00:00:00Z",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["timeframe"] == "1h"
    assert "params_hash" in body
    assert "n_trades" in body
    assert body["status"] == "completed"


@pytest.mark.asyncio
async def test_get_backtests_lists_recent(  # type: ignore[no-untyped-def]
    admin_client, monkeypatch,
) -> None:
    """GET returns a JSON list (possibly empty); supports limit/symbol/tf filters."""
    import tools.backtest as bt

    def _fake_loader(symbol, tf, start, end):  # type: ignore[no-untyped-def]
        n = 250
        idx = pd.DatetimeIndex(
            [start + timedelta(hours=i) for i in range(n)],
        )
        c = np.array([100 + i * 0.05 for i in range(n)])
        return pd.DataFrame({
            "open": c - 0.1, "high": c + 0.5, "low": c - 0.5,
            "close": c, "volume": np.full(n, 1000.0),
        }, index=idx)

    monkeypatch.setattr(bt, "_default_bars_loader", _fake_loader)

    # Seed one row via POST.
    await admin_client.post("/api/v1/admin/backtests", json={
        "symbol": "ETH/USDT", "timeframe": "1h",
        "start": "2025-01-01T00:00:00Z", "end": "2025-01-11T00:00:00Z",
    })

    resp = await admin_client.get("/api/v1/admin/backtests?limit=10")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert any(it["symbol"] == "ETH/USDT" for it in items)

    # Filter by symbol — should still return the ETH/USDT row only.
    resp_filt = await admin_client.get(
        "/api/v1/admin/backtests?symbol=ETH/USDT&timeframe=1h",
    )
    assert resp_filt.status_code == 200
    filt = resp_filt.json()
    assert all(it["symbol"] == "ETH/USDT" and it["timeframe"] == "1h" for it in filt)


# Suppress unused-import warning — datetime/timezone are kept for clarity.
_ = (datetime, timezone)
