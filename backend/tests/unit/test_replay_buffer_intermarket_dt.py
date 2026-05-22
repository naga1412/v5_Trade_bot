"""PR-BRAIN-BOOTSTRAP-FIX-2 — verify _nearest_intermarket converts ISO→datetime
before binding to asyncpg.

The asyncpg dialect rejects ISO strings for TIMESTAMPTZ parameters (sqlite is
lenient and accepts them — which is why this slipped past unit tests until
prod). These tests assert the conversion happens at the call site, so the
parameter bound to the query is a `datetime.datetime` instance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rl.replay_buffer import _nearest_intermarket


@pytest.mark.asyncio
async def test_nearest_intermarket_binds_datetime_for_z_suffix_iso() -> None:
    """ISO string ending in 'Z' is normalized to '+00:00' and converted to datetime."""
    session = MagicMock()
    session.bind = MagicMock()
    session.bind.dialect.name = "postgresql"

    result = MagicMock()
    result.first.return_value = None
    session.execute = AsyncMock(return_value=result)

    await _nearest_intermarket(
        session, symbol="ADAUSDT", opened_at_iso="2026-05-14T17:00:00Z",
    )

    call_args = session.execute.call_args
    bound_params = call_args[0][1]
    assert isinstance(bound_params["t"], datetime), (
        f"expected datetime for :t param, got {type(bound_params['t']).__name__}"
    )
    assert bound_params["t"].tzinfo is not None, (
        "datetime must be timezone-aware (asyncpg rejects naive for TIMESTAMPTZ)"
    )


@pytest.mark.asyncio
async def test_nearest_intermarket_binds_datetime_for_offset_iso() -> None:
    """ISO string with explicit +00:00 offset is parsed correctly."""
    session = MagicMock()
    session.bind = MagicMock()
    session.bind.dialect.name = "postgresql"

    result = MagicMock()
    result.first.return_value = None
    session.execute = AsyncMock(return_value=result)

    await _nearest_intermarket(
        session, symbol="BTCUSDT", opened_at_iso="2026-05-14T17:00:00+00:00",
    )

    bound_params = session.execute.call_args[0][1]
    assert isinstance(bound_params["t"], datetime)
    assert bound_params["t"] == datetime(2026, 5, 14, 17, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_nearest_intermarket_passes_string_to_sqlite_24h_query() -> None:
    """The SQLite-only second query (line 241) keeps using the ISO string —
    SQLite's `datetime(:t, '-24 hours')` SQL function expects a string."""
    session = MagicMock()
    session.bind = MagicMock()
    session.bind.dialect.name = "sqlite"

    # First call returns a row; second call (the 24h-ago query) gets invoked.
    first_result = MagicMock()
    first_row = MagicMock()
    first_row.funding_rate = 0.01
    first_row.open_interest = 1000.0
    first_result.first.return_value = first_row

    second_result = MagicMock()
    second_result.first.return_value = None

    session.execute = AsyncMock(side_effect=[first_result, second_result])

    await _nearest_intermarket(
        session, symbol="ETHUSDT", opened_at_iso="2026-05-14T17:00:00Z",
    )

    assert session.execute.call_count == 2
    first_call_params = session.execute.call_args_list[0][0][1]
    second_call_params = session.execute.call_args_list[1][0][1]

    assert isinstance(first_call_params["t"], datetime), (
        "first query (postgres-safe) must bind datetime"
    )
    assert isinstance(second_call_params["t"], str), (
        "sqlite-only second query keeps the ISO string for the datetime() SQL fn"
    )
