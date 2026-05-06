from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa

from app.core.predictor import build_prediction


@pytest.mark.integration
@pytest.mark.asyncio
async def test_funding_rate_decay_fires_when_session_supplies_negative_funding(
    bot_status_factory,
) -> None:
    """End-to-end: seed → build_prediction → trap fires."""
    now = datetime.now(timezone.utc)
    async with bot_status_factory() as session:
        await session.execute(sa.text("""
            INSERT INTO intermarket_snapshots
              (symbol, captured_at, funding_rate, mark_price,
               open_interest, source)
            VALUES
              ('BTC/USDT', :ts, -0.005, 70000.0, 1.0e9, 'binance_futures')
        """), {"ts": now})
        await session.commit()

    # Bars engineered to produce SHORT (200-bar bearish trend).
    closes = np.linspace(100.0, 50.0, 250)
    idx = pd.date_range("2026-05-01", periods=250, freq="h", tz="UTC")
    bars = pd.DataFrame({
        "open":  closes,
        "high":  closes * 1.01,
        "low":   closes * 0.99,
        "close": closes,
        "volume":np.full(250, 10.0),
    }, index=idx)

    async with bot_status_factory() as session:
        out = await build_prediction(
            symbol="BTC/USDT", timeframe="1h", bars=bars, session=session,
        )

    fires = [f for f in out.prediction_extras["fires"]
             if f["trap_id"] == "funding_rate_decay"]
    assert len(fires) == 1, f"funding_rate_decay didn't fire; fires={out.prediction_extras['fires']}"
    assert fires[0]["severity"] == "high"
    assert fires[0]["evidence"]["funding_rate"] == pytest.approx(-0.005)
