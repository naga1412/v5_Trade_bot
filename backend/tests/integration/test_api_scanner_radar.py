"""Integration tests for /api/v1/scanner/radar (SP-6 Phase A2).

Mirrors the SP-0.5 bot-status integration suite: shared ``bot_status_client``
fixture seeds a per-user predictions row pool, then asserts the
ScannerRadarOut shape.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa


async def _seed_predictions(session, *, user_id: int, n: int) -> None:
    """Seed n predictions for n distinct symbols. Even-indexed -> LONG,
    odd-indexed -> SHORT. Each gets a distinct |score| so sort order is
    deterministic.
    """
    base_ts = datetime(2026, 5, 5, 12, tzinfo=timezone.utc)
    for i in range(n):
        sym = f"SYM{i:03d}/USDT"
        direction = "LONG" if i % 2 == 0 else "SHORT"
        score = (0.55 + i * 0.001) * (1 if direction == "LONG" else -1)
        layer_scores = {
            "1": {"direction": direction, "strength": 0.6, "confidence": 0.7,
                  "notes": "Wyckoff: Accumulation"},
            "3": {"direction": direction, "strength": 0.4, "confidence": 0.5, "notes": ""},
            "4": {"direction": direction, "strength": 0.8, "confidence": 0.7,
                  "notes": "OB sweep above PDH"},
            "6": {"direction": direction, "strength": 0.3, "confidence": 0.4, "notes": ""},
            "final": {"score": score, "direction": direction,
                      "confidence": 0.7, "contributing_layers": [1, 3, 4, 6]},
            "tier": "PAPER" if abs(score) < 0.65 else "SMALL",
            "traps_fired": [],
            "static_score": score * 100,
        }
        await session.execute(sa.text(
            "INSERT INTO predictions (user_id, symbol, timeframe, ts, price, "
            "layer_scores, inputs_hash) VALUES "
            "(:u, :s, '1h', :t, :p, :ls, 'h0')"
        ), {
            "u": user_id, "s": sym,
            "t": (base_ts - timedelta(minutes=i)).isoformat(),
            "p": 100.0 + i, "ls": json.dumps(layer_scores),
        })


@pytest.mark.asyncio
async def test_radar_returns_latest_per_symbol(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=10)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=20")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "scanned_at" in body and "scanned_count" in body
    assert body["scanned_count"] == 10
    assert "filter_counts" in body and "all" in body["filter_counts"]
    assert isinstance(body["bullish"], list) and isinstance(body["bearish"], list)
    assert len(body["bullish"]) == 5
    assert len(body["bearish"]) == 5


@pytest.mark.asyncio
async def test_radar_sorted_by_abs_score(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=4)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=10")
    body = r.json()
    bullish_scores = [c["ai_score"] for c in body["bullish"]]
    bearish_scores = [abs(c["ai_score"]) for c in body["bearish"]]
    assert bullish_scores == sorted(bullish_scores, reverse=True)
    assert bearish_scores == sorted(bearish_scores, reverse=True)


@pytest.mark.asyncio
async def test_radar_per_user_isolation(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    """User 1's radar never returns user 2's predictions."""
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=2)
        await _seed_predictions(s, user_id=2, n=3)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=10")
    body = r.json()
    # bot_status_client is wired to user_id=1 -> only the 2 seeded rows.
    assert body["scanned_count"] == 2


@pytest.mark.asyncio
async def test_radar_signal_card_fields(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=2)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=10")
    card = r.json()["bullish"][0]
    required = {"symbol", "full_name", "points", "pct_change", "direction",
                "signal_tier", "ai_score", "confidence", "scores", "sparkline",
                "wyckoff_phase"}
    assert required <= set(card.keys()), f"missing: {required - set(card.keys())}"
    assert card["direction"] in ("LONG", "SHORT")
    assert isinstance(card["sparkline"], list)
    assert isinstance(card["scores"], dict)
    assert {"smc", "wyckoff", "microstructure", "momentum"} <= set(card["scores"].keys())


@pytest.mark.asyncio
async def test_radar_filter_counts(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=10)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h&limit=50")
    fc = r.json()["filter_counts"]
    assert fc["all"] == 10
    # Confirmed + probable + weak should partition all rows (each row falls
    # into exactly one bucket given current tier mapping).
    assert fc["confirmed"] + fc["probable"] + fc["weak"] == fc["all"]


@pytest.mark.asyncio
async def test_radar_limit_clamped(  # type: ignore[no-untyped-def]
    bot_status_client, bot_status_factory,
):
    async with bot_status_factory() as s:
        await _seed_predictions(s, user_id=1, n=20)
        await s.commit()
    r = await bot_status_client.get("/api/v1/scanner/radar?limit=5")
    body = r.json()
    assert len(body["bullish"]) + len(body["bearish"]) <= 5


@pytest.mark.asyncio
async def test_radar_empty_when_no_predictions(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h")
    assert r.status_code == 200
    body = r.json()
    assert body["scanned_count"] == 0
    assert body["bullish"] == []
    assert body["bearish"] == []


@pytest.mark.asyncio
async def test_radar_unknown_market_returns_400(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    r = await bot_status_client.get("/api/v1/scanner/radar?market=alien&tf=1h")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_radar_unknown_timeframe_returns_400(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=99x")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_radar_requires_authenticated_user(  # type: ignore[no-untyped-def]
    bot_status_client,
):
    """The bot_status_client fixture overrides require_user - but the route
    must still go through current_user_or_impersonated. This test confirms the
    handler signature is wired correctly (i.e. removing the dep would surface
    a 422 from unresolved User param)."""
    r = await bot_status_client.get("/api/v1/scanner/radar?market=crypto&tf=1h")
    assert r.status_code == 200
