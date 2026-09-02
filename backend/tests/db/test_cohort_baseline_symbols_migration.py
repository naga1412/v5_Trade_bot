from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)


@pytest.mark.asyncio
async def test_cohort_baseline_symbols_table_shape() -> None:
    """symbol PK, both count columns NOT NULL, frozen_at defaults to now()."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        cols = {
            r.column_name: r
            for r in (await conn.execute(sa.text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'cohort_baseline_symbols'"
            ))).all()
        }
        assert set(cols) == {"symbol", "pred_distinct_days", "pred_n", "frozen_at"}
        assert cols["symbol"].is_nullable == "NO"
        assert cols["pred_distinct_days"].is_nullable == "NO"
        assert cols["pred_n"].is_nullable == "NO"
        assert cols["frozen_at"].is_nullable == "NO"
        assert cols["frozen_at"].column_default is not None

        pk_cols = (await conn.execute(sa.text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'cohort_baseline_symbols'::regclass AND i.indisprimary"
        ))).all()
        assert [r.attname for r in pk_cols] == ["symbol"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_cohort_baseline_symbols_seeded_correctly() -> None:
    """73 unique symbols; the 6 confirmed stablecoin/synthetic exclusions
    absent; the 5 confirmed-CLEARS disputed symbols present; the 2
    confirmed-FAILS disputed symbols absent. Matches baseline-final-
    recount's verified staging output exactly (2026-08-30 operator
    ruling) -- this is the locked list, not re-derivable from live data."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT symbol FROM cohort_baseline_symbols"
        ))).all()
    await engine.dispose()

    symbols = {r.symbol for r in rows}
    assert len(rows) == 73
    assert len(symbols) == 73  # no duplicates

    stablecoin_synthetic_exclusions = {
        "UUSDT", "SKHYBUSDT", "SOXLBUSDT", "KORUBUSDT", "SNXXBUSDT", "EURIUSDT",
    }
    assert symbols.isdisjoint(stablecoin_synthetic_exclusions)

    confirmed_clears = {"XPLUSDT", "NEARUSDT", "ADAUSDT", "BICOUSDT", "PLUMEUSDT"}
    assert confirmed_clears <= symbols

    confirmed_fails = {"REDUSDT", "TRUMPUSDT"}
    assert symbols.isdisjoint(confirmed_fails)

    # The permanent, full-window core -- present on essentially every
    # pre-cutover day. If these are ever missing the seed data is wrong.
    full_window_core = {
        "BTCUSDT", "BNBUSDT", "DOGEUSDT", "ETHUSDT", "SOLUSDT",
        "TRXUSDT", "XRPUSDT", "ZECUSDT",
    }
    assert full_window_core <= symbols
