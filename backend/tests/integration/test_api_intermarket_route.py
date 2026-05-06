from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_intermarket_route_returns_latest(
    test_client, bot_status_factory, authed_user_token,
) -> None:
    now = datetime.now(timezone.utc)
    async with bot_status_factory() as session:
        await session.execute(sa.text("""
            INSERT INTO intermarket_snapshots
              (symbol, captured_at, funding_rate, mark_price,
               open_interest, source)
            VALUES
              ('BTC/USDT', :t_old, 0.0, 68000.0, 1.0e9, 'binance_futures'),
              ('BTC/USDT', :t_now, -0.0012, 70000.0, 1.30e9, 'binance_futures')
        """), {"t_old": now - timedelta(hours=24), "t_now": now})
        await session.commit()

    fake_corr = (-0.42, 0.18)
    with patch(
        "app.api.routes.intermarket.compute_30d_correlations",
        new=AsyncMock(return_value=fake_corr),
    ):
        resp = test_client.get(
            "/api/v1/intermarket/BTC%2FUSDT",
            headers={"Authorization": f"Bearer {authed_user_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["funding_rate"] == pytest.approx(-0.0012)
    assert body["mark_price"] == pytest.approx(70000.0)
    assert body["open_interest"] == pytest.approx(1.30e9)
    assert body["open_interest_delta_24h_pct"] == pytest.approx(0.30)
    assert body["dxy_correlation_30d"] == pytest.approx(-0.42)
    assert body["gold_correlation_30d"] == pytest.approx(0.18)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_intermarket_route_404_when_no_snapshot(
    test_client, bot_status_factory, authed_user_token,
) -> None:
    resp = test_client.get(
        "/api/v1/intermarket/UNKNOWN%2FUSDT",
        headers={"Authorization": f"Bearer {authed_user_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_intermarket_route_unauthed_401(
    test_client,
) -> None:
    resp = test_client.get("/api/v1/intermarket/BTC%2FUSDT")
    assert resp.status_code == 401
