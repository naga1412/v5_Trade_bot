import numpy as np
import pytest
import pandas as pd
from datetime import datetime, timezone

from app.api.routes import tab1
from app.core.dataquality.validator import Candle

# ---------------------------------------------------------------------------
# GRANDFATHERED NETWORK ACCESS -- PRE-EXISTING DEBT, NOT APPROVAL.
# ---------------------------------------------------------------------------
# This module reaches the real internet. It was already doing so before the
# suite-wide egress block landed (2026-10-26); the block did not create this
# dependency, it REVEALED it. 51 tests across 10 files were found this way,
# all routing through the real build_prediction path, which fetches live
# market data.
#
# WHY MARKED RATHER THAN FIXED HERE: mocking the transport would change what
# each test actually verifies, across ten files, and that is its own piece
# of work with its own review. Marking makes the dependency EXPLICIT AND
# ENUMERATED instead of invisible, and keeps the guard live for every other
# test -- which is the point: a NEW test that reaches the network now fails
# immediately instead of waiting to be noticed.
#
# PARTITIONED 2026-10-27, BEFORE anyone pays this down: 51 STRUCTURE,
# 0 VALUE. Every one of the 51 asserts on fixture-injected values (seeded
# shadow_trades rows, monkeypatched ghost candles, seeded PatternStatsLookup,
# seeded news with a FROZEN clock) or on a quantity's definitional range.
# Live data is an INPUT to these tests but cannot change any verdict, so
# there is NO false-green risk from market movement -- which is the thing
# that would have made this a project rather than a chore.
#
# Cross-checked against CI history: ZERO flakes among the 51 across the last
# 80 runs (4 failures total -- 2 from the commit that introduced the egress
# guard, 2 from TimescaleDB service-container startup, none a test in this
# set).
#
# WHAT IT STILL COSTS, so nobody rediscovers it: a fetch failure fails the
# test, so these cannot run in a sandboxed or offline runner, and CI runners
# reach Binance from SHARED GitHub IP RANGES -- meaning the suite depends on
# an external service's tolerance of OTHER TENANTS' traffic. The failure mode
# is not "our calls are too frequent", it is a 418/429 earned by somebody
# else. That is why the debt is real rather than cosmetic, even at 0 VALUE.
#
# Low risk to pay down: swap the fetch for a fixture and the assertions are
# unchanged. Removing a file from this list is the goal. Adding one is not.
# NOTE: marks are PER-TEST below, not module-level. A blanket
# pytestmark here granted network to 99 tests when 51 need it --
# the grant must be no broader than the evidence.


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


@pytest.mark.allow_network
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


@pytest.mark.allow_network
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
        "user_id": 1,
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
async def test_predict_signal_markers_isolated_per_user(
    monkeypatch, bot_status_client, bot_status_factory,
) -> None:
    """User 1 must NOT see user 2's signal markers when querying ?signal=."""
    import json
    from datetime import UTC

    import sqlalchemy as sa
    from app.db.audit import insert_with_chain

    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 250))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    # Seed user 2 + a shadow trade for them with a unique signal_id.
    async with bot_status_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO users (id, email, display_name, is_admin) "
            "VALUES (2, 'other@local', 'Other', 0)"
        ))
        await session.commit()

    closed_at = datetime(2026, 5, 2, 12, tzinfo=UTC)
    payload = {
        "user_id": 2,
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
        "signal_id": "user2-only-sig",
    }
    async with bot_status_factory() as session:
        await insert_with_chain(session, "shadow_trades", payload)
        await session.commit()

    # bot_status_client is bound to user 1 — must 404 on user 2's signal_id.
    r = await bot_status_client.get(
        "/api/v1/predict/BTC-USDT/1h?signal=user2-only-sig"
    )
    assert r.status_code == 404


@pytest.mark.allow_network
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


@pytest.mark.allow_network
@pytest.mark.asyncio
async def test_predict_attaches_ghost_when_active_checkpoint_loaded(
    monkeypatch, bot_status_client,
) -> None:
    """SP-1: when an ML checkpoint is loaded, REST initial-fetch must
    return ghost data (previously only the WS push path included it,
    leaving the dashboard stuck on "no model" until the next candle close).
    """
    from app.ml.inference import GhostCandle

    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 260))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    fake_ghost = GhostCandle(
        open=150.0, high=152.0, low=148.0, close=151.0,
        p5_low=147.0, p95_high=153.0, uncertainty=1.5,
    )
    monkeypatch.setattr(
        tab1, "get_active_model_and_checkpoint",
        lambda: (object(), object()),
    )
    monkeypatch.setattr(
        tab1, "predict_ghost_candle",
        lambda **kwargs: fake_ghost,
    )

    r = await bot_status_client.get("/api/v1/predict/BTC-USDT/1h")
    assert r.status_code == 200
    body = r.json()
    assert body["ghost"] is not None
    assert body["ghost"]["close"] == 151.0
    assert body["ghost"]["p5_low"] == 147.0
    assert body["ghost"]["p95_high"] == 153.0


@pytest.mark.allow_network
@pytest.mark.asyncio
async def test_predict_omits_ghost_when_no_active_checkpoint(
    monkeypatch, bot_status_client,
) -> None:
    """When no checkpoint is loaded, ghost stays None (frontend renders "no model")."""
    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 260))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)
    monkeypatch.setattr(tab1, "get_active_model_and_checkpoint", lambda: None)

    r = await bot_status_client.get("/api/v1/predict/BTC-USDT/1h")
    assert r.status_code == 200
    assert r.json()["ghost"] is None


@pytest.mark.allow_network
@pytest.mark.asyncio
async def test_predict_l8_abstains_by_default_even_with_ghost(
    monkeypatch, bot_status_client,
) -> None:
    """L8_GHOST_SCORING_ENABLED defaults False: ghost still displays (UI
    unaffected) but layer_scores["8"] stays None — the aggregator does not
    see it, matching pre-wiring behaviour bit-for-bit.
    """
    from app.ml.inference import GhostCandle

    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 260))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)
    monkeypatch.setattr(
        tab1, "get_active_model_and_checkpoint",
        lambda: (object(), object()),
    )
    monkeypatch.setattr(
        tab1, "predict_ghost_candle",
        lambda **kwargs: GhostCandle(
            open=150.0, high=152.0, low=148.0, close=151.0,
            p5_low=147.0, p95_high=153.0, uncertainty=1.5,
        ),
    )

    r = await bot_status_client.get("/api/v1/predict/BTC-USDT/1h")
    assert r.status_code == 200
    body = r.json()
    assert body["ghost"] is not None  # display path unaffected
    assert body["layer_scores"]["8"] is None  # scoring path untouched


@pytest.mark.allow_network
@pytest.mark.asyncio
async def test_predict_l8_scores_when_flag_enabled(
    monkeypatch, bot_status_client,
) -> None:
    """With L8_GHOST_SCORING_ENABLED=True and an active checkpoint, the
    ghost vote reaches build_prediction and layer_scores["8"] is populated.
    """
    from app.config import get_settings
    from app.ml.inference import GhostCandle

    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 260))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)
    monkeypatch.setattr(
        tab1, "get_active_model_and_checkpoint",
        lambda: (object(), object()),
    )
    monkeypatch.setattr(
        tab1, "predict_ghost_candle",
        lambda **kwargs: GhostCandle(
            open=150.0, high=152.0, low=148.0, close=250.0,
            p5_low=147.0, p95_high=253.0, uncertainty=0.1,
        ),
    )
    monkeypatch.setattr(get_settings(), "L8_GHOST_SCORING_ENABLED", True)
    r = await bot_status_client.get("/api/v1/predict/BTC-USDT/1h")

    assert r.status_code == 200
    body = r.json()
    assert body["layer_scores"]["8"] is not None
    assert body["layer_scores"]["8"]["direction"] == "LONG"
