import numpy as np
import pytest
import httpx
import pandas as pd
from datetime import datetime, timezone

from app.main import app
from app.api.routes import tab1
from app.core.dataquality.validator import Candle


def _fake_candles(n: int = 250) -> list[Candle]:
    closes = list(np.linspace(100.0, 200.0, n))
    return [
        Candle(
            symbol="BTC/USDT", timeframe="1h",
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc) + pd.Timedelta(hours=i),
            open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]


@pytest.mark.asyncio
async def test_predict_returns_full_payload(monkeypatch) -> None:
    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 250))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/predict/BTC-USDT/1h")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["timeframe"] == "1h"
    assert body["final"]["direction"] in {"LONG", "SHORT", "NEUTRAL"}
    assert "rsi" in body["momentum"]
    assert body["inputs_hash"]


@pytest.mark.asyncio
async def test_predict_unknown_symbol_returns_404(monkeypatch) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/predict/XXX-YYY/1h")
    assert r.status_code == 404
