import numpy as np
import pytest
import pandas as pd
from datetime import datetime, timezone

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
async def test_predict_returns_full_payload(monkeypatch, bot_status_client) -> None:
    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 250))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    r = await bot_status_client.get("/api/v1/predict/BTC-USDT/1h")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["timeframe"] == "1h"
    assert body["final"]["direction"] in {"LONG", "SHORT", "NEUTRAL"}
    assert "rsi" in body["momentum"]
    assert body["inputs_hash"]


@pytest.mark.asyncio
async def test_predict_unknown_symbol_returns_404(monkeypatch, bot_status_client) -> None:
    r = await bot_status_client.get("/api/v1/predict/XXX-YYY/1h")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_predict_with_unknown_signal_returns_404(
    monkeypatch, bot_status_client,
) -> None:
    """When ?signal= references a non-existent signal_id, the route 404s."""
    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 250))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    r = await bot_status_client.get(
        "/api/v1/predict/BTC-USDT/1h?signal=NONEXISTENT"
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_predict_with_valid_signal_attaches_markers(
    monkeypatch, bot_status_client, bot_status_factory,
) -> None:
    """A valid ?signal=<id> populates the response's signal_markers field."""
    import json
    from datetime import UTC

    from app.db.audit import insert_with_chain

    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 250))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    closed_at = datetime(2026, 5, 2, 12, tzinfo=UTC)
    payload = {
        "symbol": "BTC/USDT", "timeframe": "1h", "direction": "LONG",
        "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
        "position_size_usdt": 30.0,
        "entry_score": 0.6, "entry_confidence": 0.7,
        "layer_scores": json.dumps({}), "entry_atr": 1.0,
        "exit_price": 110.0, "exit_reason": "TAKE_PROFIT",
        "pnl_pct": 10.0, "pnl_usdt": 3.0, "bars_held": 4,
        "opened_at": (closed_at - pd.Timedelta(hours=4)).isoformat(),
        "closed_at": closed_at.isoformat(),
        "inputs_hash": "h" * 64, "model_version": "sp-0.5",
        "signal_id": "deeplink-sig-1",
    }
    async with bot_status_factory() as session:
        await insert_with_chain(session, "shadow_trades", payload)
        await session.commit()

    r = await bot_status_client.get(
        "/api/v1/predict/BTC-USDT/1h?signal=deeplink-sig-1"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signal_markers"] is not None
    sm = body["signal_markers"]
    assert sm["signal_id"] == "deeplink-sig-1"
    assert sm["direction"] == "LONG"
    assert sm["entry"] == pytest.approx(100.0)
    assert sm["stop_loss"] == pytest.approx(95.0)
    assert sm["take_profit"] == pytest.approx(110.0)
    assert sm["exit_reason"] == "TAKE_PROFIT"
    assert sm["exit_price"] == pytest.approx(110.0)


@pytest.mark.asyncio
async def test_predict_without_signal_omits_markers(
    monkeypatch, bot_status_client,
) -> None:
    """Without ?signal=, the response keeps signal_markers as null (back-compat)."""
    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 250))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    r = await bot_status_client.get("/api/v1/predict/BTC-USDT/1h")
    assert r.status_code == 200
    assert r.json()["signal_markers"] is None
