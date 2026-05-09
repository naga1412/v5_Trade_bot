"""SP-8 Phase J — /api/v1/me/tax/{summary,export} integration tests."""
from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa


async def _seed_tax_event(
    auth_factory, *,
    user_id: int,
    fy_year: str = "FY2026-27",
    realized_pnl_inr: float = 12345.67,
    tds_owed_inr: float = 247.50,
) -> None:
    async with auth_factory() as s:
        await s.execute(sa.text(
            "INSERT INTO tax_events ("
            "trade_id, user_id, symbol, direction, quantity, "
            "entry_price, exit_price, entry_value_inr, exit_value_inr, "
            "realized_pnl_inr, tds_owed_inr, fee_paid_inr, leverage, "
            "exchange, fy_year, closed_at, prev_hash, row_hash) "
            "VALUES (1, :u, 'BTC/USDT', 'LONG', 0.005, "
            "        80000, 82000, 33200000, 34030000, "
            "        :pnl, :tds, 50, 5, "
            "        'binance', :fy, '2026-05-09T10:00:00+00:00', "
            "        'prev', :rh)"
        ), {"u": user_id, "pnl": realized_pnl_inr, "tds": tds_owed_inr,
            "fy": fy_year, "rh": f"row-{fy_year}-{user_id}"})
        await s.commit()


@pytest.mark.asyncio
async def test_summary_zero_when_no_events(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.get("/api/v1/me/tax/summary?fy_year=FY2026-27")
    assert r.status_code == 200
    body = r.json()
    assert body["fy_year"] == "FY2026-27"
    assert body["total_trades"] == 0
    assert body["total_realized_pnl_inr"] == 0
    assert body["total_tds_inr"] == 0


@pytest.mark.asyncio
async def test_summary_aggregates_from_events(
    friend_client: httpx.AsyncClient, auth_factory,
) -> None:
    me = (await friend_client.get("/api/v1/me")).json()
    await _seed_tax_event(auth_factory, user_id=me["id"])

    r = await friend_client.get("/api/v1/me/tax/summary?fy_year=FY2026-27")
    body = r.json()
    assert body["total_trades"] == 1
    assert body["total_realized_pnl_inr"] == 12345.67
    assert body["total_tds_inr"] == 247.50


@pytest.mark.asyncio
async def test_export_returns_csv_with_attachment_header(
    friend_client: httpx.AsyncClient, auth_factory,
) -> None:
    me = (await friend_client.get("/api/v1/me")).json()
    await _seed_tax_event(auth_factory, user_id=me["id"])

    r = await friend_client.get("/api/v1/me/tax/export?fy_year=FY2026-27")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"].lower()
    assert "schedule-vda-FY2026-27.csv" in r.headers["content-disposition"]
    text = r.text
    assert "Date of Sale" in text
    assert "BTC/USDT" in text
    assert "12345.67" in text


@pytest.mark.asyncio
async def test_export_empty_fy_returns_header_only_csv(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.get("/api/v1/me/tax/export?fy_year=FY2025-26")
    assert r.status_code == 200
    text = r.text
    # Header line only, no data rows.
    assert text.strip().startswith("Date of Sale")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_summary_filters_by_user(
    friend_client: httpx.AsyncClient, auth_factory,
) -> None:
    """Other users' tax_events must NOT show up in this user's summary."""
    me = (await friend_client.get("/api/v1/me")).json()
    await _seed_tax_event(auth_factory, user_id=999, realized_pnl_inr=99999)
    await _seed_tax_event(auth_factory, user_id=me["id"], realized_pnl_inr=42)

    r = await friend_client.get("/api/v1/me/tax/summary?fy_year=FY2026-27")
    body = r.json()
    assert body["total_trades"] == 1
    assert body["total_realized_pnl_inr"] == 42
