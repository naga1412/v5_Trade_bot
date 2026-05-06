"""Integration tests for ``GET /api/v1/intermarket/{symbol}`` (SP-3.5 Phase E2).

Mirrors the SP-9 ``test_api_admin_news_list.py`` pattern: seed
``intermarket_snapshots`` rows via the ``auth_factory`` session, then drive
the route through the ``admin_client`` fixture defined in
``tests/integration/conftest.py``. The fixture overrides the auth deps so no
``Authorization: Bearer`` header is needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa


async def _seed_snapshots(
    factory: object,
    *,
    symbol: str,
    rows: list[dict],
) -> None:
    """INSERT a list of ``intermarket_snapshots`` rows for ``symbol``."""
    async with factory() as session:  # type: ignore[operator]
        for r in rows:
            await session.execute(
                sa.text(
                    "INSERT INTO intermarket_snapshots "
                    "(symbol, captured_at, funding_rate, mark_price, "
                    " open_interest, source) "
                    "VALUES (:symbol, :captured_at, :funding_rate, "
                    "        :mark_price, :open_interest, :source)"
                ),
                {
                    "symbol": symbol,
                    "captured_at": r["captured_at"],
                    "funding_rate": r.get("funding_rate"),
                    "mark_price": r.get("mark_price"),
                    "open_interest": r.get("open_interest"),
                    "source": r.get("source", "binance_futures"),
                },
            )
        await session.commit()


@pytest.mark.asyncio
async def test_get_intermarket_route_returns_latest(  # type: ignore[no-untyped-def]
    admin_client, auth_factory,
) -> None:
    now = datetime.now(timezone.utc)
    # Pass datetime objects (not isoformat strings) so SQLAlchemy binds them
    # the same way the persistence layer's WHERE clause will format the
    # comparison value — string lexicographic compare otherwise mismatches.
    await _seed_snapshots(auth_factory, symbol="BTC/USDT", rows=[
        {
            "captured_at": (now - timedelta(hours=24)).replace(microsecond=0),
            "funding_rate": 0.0,
            "mark_price": 68000.0,
            "open_interest": 1.0e9,
        },
        {
            "captured_at": now.replace(microsecond=0),
            "funding_rate": -0.0012,
            "mark_price": 70000.0,
            "open_interest": 1.30e9,
        },
    ])

    fake_corr = (-0.42, 0.18)
    with patch(
        "app.api.routes.intermarket.compute_30d_correlations",
        new=AsyncMock(return_value=fake_corr),
    ):
        resp = await admin_client.get("/api/v1/intermarket/BTC%2FUSDT")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["funding_rate"] == pytest.approx(-0.0012)
    assert body["mark_price"] == pytest.approx(70000.0)
    assert body["open_interest"] == pytest.approx(1.30e9)
    # (1.30e9 - 1.0e9) / 1.0e9 = 0.30
    assert body["open_interest_delta_24h_pct"] == pytest.approx(0.30)
    assert body["dxy_correlation_30d"] == pytest.approx(-0.42)
    assert body["gold_correlation_30d"] == pytest.approx(0.18)


@pytest.mark.asyncio
async def test_get_intermarket_route_404_when_no_snapshot(  # type: ignore[no-untyped-def]
    admin_client,
) -> None:
    fake_corr = (None, None)
    with patch(
        "app.api.routes.intermarket.compute_30d_correlations",
        new=AsyncMock(return_value=fake_corr),
    ):
        resp = await admin_client.get("/api/v1/intermarket/UNKNOWN%2FUSDT")
    assert resp.status_code == 404
