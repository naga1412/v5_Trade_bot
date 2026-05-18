"""PR9 — /bot-status/sizing endpoint integration tests.

Read-only diagnostic: surfaces what compute_dynamic_size would return
for a hypothetical signal given the user's current balance + tier.
"""
from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_sizing_endpoint_returns_disabled_flag_by_default(
    bot_status_client: Any,
) -> None:
    """DYNAMIC_SIZING_ENABLED=False (default) → flag mirrors that;
    sample_margin_usdt is None (sizing path returns None when off)."""
    r = await bot_status_client.get("/api/v1/bot-status/sizing")
    assert r.status_code == 200
    body = r.json()
    assert body["dynamic_sizing_enabled"] is False
    assert body["sample_margin_usdt"] is None


@pytest.mark.asyncio
async def test_sizing_endpoint_classifies_small_tier_for_default_balance(
    bot_status_client: Any,
) -> None:
    """Conftest's seed user has fixed_size_min_usdt=10 → balance proxy
    = max(50, 100) = 100 → tier "small"."""
    r = await bot_status_client.get("/api/v1/bot-status/sizing")
    body = r.json()
    assert body["tier"] == "small"
    assert body["tier_max_fraction"] == 0.01


@pytest.mark.asyncio
async def test_sizing_endpoint_surfaces_quarter_kelly_default(
    bot_status_client: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/sizing")
    assert r.json()["fractional_kelly"] == 0.25


@pytest.mark.asyncio
async def test_sizing_endpoint_default_confidence_is_70(
    bot_status_client: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/sizing")
    assert r.json()["sample_confidence_pct"] == 70.0


@pytest.mark.asyncio
async def test_sizing_endpoint_accepts_custom_confidence_pct(
    bot_status_client: Any,
) -> None:
    r = await bot_status_client.get(
        "/api/v1/bot-status/sizing?confidence_pct=85",
    )
    assert r.status_code == 200
    assert r.json()["sample_confidence_pct"] == 85.0


@pytest.mark.asyncio
async def test_sizing_endpoint_rejects_out_of_range_confidence(
    bot_status_client: Any,
) -> None:
    """confidence_pct must be in [0, 100]."""
    r = await bot_status_client.get(
        "/api/v1/bot-status/sizing?confidence_pct=150",
    )
    assert r.status_code == 422  # FastAPI validation rejection
