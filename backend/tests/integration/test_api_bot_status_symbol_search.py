"""Integration tests for GET /api/v1/bot-status/symbol-search."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa


async def _seed_symbols(
    factory: Any, *, rows: list[tuple[str, str, str]],
) -> None:
    """rows = [(exchange, symbol, asset_class), ...]"""
    async with factory() as s:
        for ex, sym, ac in rows:
            await s.execute(sa.text(
                "INSERT INTO universe_history "
                "(exchange, symbol, asset_class, listed_at, last_synced_at) "
                "VALUES (:e, :s, :a, "
                "        '2024-01-01T00:00:00+00:00', "
                "        '2026-05-01T00:00:00+00:00')"
            ), {"e": ex, "s": sym, "a": ac})
        await s.commit()


@pytest.mark.asyncio
async def test_returns_empty_list_when_no_match(
    bot_status_client: httpx.AsyncClient,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/symbol-search?q=ZZZZ")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "ZZZZ"
    assert body["hits"] == []


@pytest.mark.asyncio
async def test_prefix_match_ranks_before_substring(
    bot_status_client: httpx.AsyncClient, bot_status_factory,
) -> None:
    await _seed_symbols(bot_status_factory, rows=[
        ("binance", "SHIB/USDT", "crypto"),
        ("binance", "SUSHI/USDT", "crypto"),     # contains 'SHI' as substring
        ("binance", "1000SHIB/USDT", "crypto"),  # contains 'SHIB' as substring
    ])
    r = await bot_status_client.get(
        "/api/v1/bot-status/symbol-search?q=shib&limit=5",
    )
    assert r.status_code == 200
    body = r.json()
    symbols = [h["symbol"] for h in body["hits"]]
    # SHIB/USDT (prefix) ranks before 1000SHIB/USDT (substring).
    assert symbols.index("SHIB/USDT") < symbols.index("1000SHIB/USDT")
    assert "SHIB/USDT" in symbols
    # SUSHI does not contain "SHIB" — must NOT be in the hit list.
    assert "SUSHI/USDT" not in symbols


@pytest.mark.asyncio
async def test_query_is_case_insensitive(
    bot_status_client: httpx.AsyncClient, bot_status_factory,
) -> None:
    await _seed_symbols(bot_status_factory, rows=[
        ("binance", "SHIB/USDT", "crypto"),
    ])
    for q in ("shib", "SHIB", "Shib", "sHiB"):
        r = await bot_status_client.get(
            f"/api/v1/bot-status/symbol-search?q={q}",
        )
        assert r.status_code == 200
        symbols = [h["symbol"] for h in r.json()["hits"]]
        assert "SHIB/USDT" in symbols, f"failed for q={q!r}"


@pytest.mark.asyncio
async def test_query_strips_unsafe_chars_safely(
    bot_status_client: httpx.AsyncClient, bot_status_factory,
) -> None:
    """Pasting wildcards / SQL syntax doesn't break or amplify."""
    await _seed_symbols(bot_status_factory, rows=[
        ("binance", "SHIB/USDT", "crypto"),
    ])
    # Percent / underscore are LIKE wildcards; we want them treated
    # as literal (or stripped by the sanitiser) so they don't match
    # anything unintended.
    r = await bot_status_client.get(
        "/api/v1/bot-status/symbol-search?q=%25%20OR%201%3D1",  # "% OR 1=1"
    )
    assert r.status_code == 200
    # Sanitiser stripped non-symbol chars; the residual "OR1=1" doesn't
    # match SHIB; expect empty hits, NOT every symbol.
    assert r.json()["hits"] == []


@pytest.mark.asyncio
async def test_excludes_delisted_symbols(
    bot_status_client: httpx.AsyncClient, bot_status_factory,
) -> None:
    async with bot_status_factory() as s:
        await s.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, "
            " delisted_at, last_synced_at) "
            "VALUES ('binance', 'OLD/USDT', 'crypto', "
            "        '2020-01-01T00:00:00+00:00', "
            "        '2025-01-01T00:00:00+00:00', "
            "        '2026-05-01T00:00:00+00:00')"
        ))
        await s.commit()
    r = await bot_status_client.get(
        "/api/v1/bot-status/symbol-search?q=OLD",
    )
    assert r.status_code == 200
    assert r.json()["hits"] == []


@pytest.mark.asyncio
async def test_limit_is_clamped(
    bot_status_client: httpx.AsyncClient, bot_status_factory,
) -> None:
    rows = [
        ("binance", f"AAA{i}/USDT", "crypto")
        for i in range(50)
    ]
    await _seed_symbols(bot_status_factory, rows=rows)
    r = await bot_status_client.get(
        "/api/v1/bot-status/symbol-search?q=AAA&limit=5",
    )
    assert r.status_code == 200
    assert len(r.json()["hits"]) == 5
    # Server-side hard cap is 25 even if the client asks for more.
    r2 = await bot_status_client.get(
        "/api/v1/bot-status/symbol-search?q=AAA&limit=999",
    )
    # FastAPI returns 422 when ge/le validation fails.
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_optional_exchange_filter(
    bot_status_client: httpx.AsyncClient, bot_status_factory,
) -> None:
    await _seed_symbols(bot_status_factory, rows=[
        ("binance", "SHIB/USDT", "crypto"),
        ("bybit",   "SHIB/USDT", "crypto"),
    ])
    r = await bot_status_client.get(
        "/api/v1/bot-status/symbol-search?q=SHIB&exchange=bybit",
    )
    body = r.json()
    assert all(h["exchange"] == "bybit" for h in body["hits"])
    assert any(h["symbol"] == "SHIB/USDT" for h in body["hits"])


@pytest.mark.asyncio
async def test_24h_volume_attached_when_present(
    bot_status_client: httpx.AsyncClient, bot_status_factory,
) -> None:
    await _seed_symbols(bot_status_factory, rows=[
        ("binance", "SHIB/USDT", "crypto"),
    ])
    async with bot_status_factory() as s:
        await s.execute(sa.text(
            "INSERT INTO asset_universe "
            "(symbol, quote_volume_usd_24h, rank, snapshot_at) "
            "VALUES ('SHIB/USDT', 12345678.0, 17, "
            "        '2026-05-09T00:00:00+00:00')"
        ))
        await s.commit()
    r = await bot_status_client.get(
        "/api/v1/bot-status/symbol-search?q=shib",
    )
    hit = next(h for h in r.json()["hits"] if h["symbol"] == "SHIB/USDT")
    assert hit["quote_volume_24h_usdt"] == 12345678.0
