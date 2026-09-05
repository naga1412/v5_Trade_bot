"""Telegram dispatch dedup gate — unit tests.

The fail-open contract is critical: a DB read failure must NOT block a
real signal from sending. Scope is pinned by test_dispatcher_telegram_
dedup_scope.py (integration-level, not here) — this file only covers
the gate function's own logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.trading.execution.telegram_dedup_gate import _check_telegram_dedup

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def _session_returning(row: SimpleNamespace | None) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.one_or_none = MagicMock(return_value=row)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_gate_disabled_returns_none_without_db_call() -> None:
    """cooldown_hours=None must short-circuit before touching the DB."""
    session = MagicMock()
    session.execute = AsyncMock()
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=None, now_fn=lambda: _NOW,
    )
    assert result is None
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_gate_no_prior_send_returns_none() -> None:
    session = _session_returning(SimpleNamespace(last_sent_at=None))
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_row_none_returns_none() -> None:
    """one_or_none() itself returning None (e.g. empty result set edge
    case) must be treated the same as no prior send, not raise."""
    session = _session_returning(None)
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_recent_send_within_window_suppresses() -> None:
    row = SimpleNamespace(last_sent_at=_NOW - timedelta(hours=2))
    session = _session_returning(row)
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    assert result is not None
    assert "LTCUSDT" in result
    assert "LONG" in result


@pytest.mark.asyncio
async def test_gate_send_outside_window_returns_none() -> None:
    row = SimpleNamespace(last_sent_at=_NOW - timedelta(hours=7))
    session = _session_returning(row)
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_exactly_at_boundary_returns_none() -> None:
    """elapsed >= cooldown must allow the send (>=, not >)."""
    row = SimpleNamespace(last_sent_at=_NOW - timedelta(hours=6))
    session = _session_returning(row)
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_naive_datetime_from_db_treated_as_utc() -> None:
    """asyncpg/SQLite can round-trip a naive datetime (no tzinfo) —
    must be treated as UTC, not raise on the subtraction."""
    row = SimpleNamespace(last_sent_at=(_NOW - timedelta(hours=2)).replace(tzinfo=None))
    session = _session_returning(row)
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    assert result is not None


@pytest.mark.asyncio
async def test_gate_keyed_on_direction_reversal_recent_opposite_direction_still_sends() -> None:
    """A LONG sent 1h ago must NOT suppress a SHORT for the same symbol —
    the query itself is scoped to (symbol, direction), verify the SQL
    params reflect that rather than assuming."""
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result_mock)

    await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="SHORT",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    call_args = session.execute.call_args
    params = call_args[0][1]
    assert params["s"] == "LTCUSDT"
    assert params["d"] == "SHORT"


@pytest.mark.asyncio
async def test_gate_db_error_fails_open() -> None:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("connection reset"))
    result = await _check_telegram_dedup(
        session=session, symbol="LTCUSDT", direction="LONG",
        cooldown_hours=6.0, now_fn=lambda: _NOW,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Settings validator — TELEGRAM_DEDUP_COOLDOWN_HOURS
# ---------------------------------------------------------------------------


def test_settings_default_is_12_hours() -> None:
    """Operator decision, 2026-09-04: 12h ships as the default -- matches
    shadow's median 8.1-bar-to-stop / 12.0-bar-to-TP lifetime on the 1h
    lane, chosen on mechanism not on the suppression percentage alone."""
    from app.config import Settings

    s = Settings()
    assert s.TELEGRAM_DEDUP_COOLDOWN_HOURS == 12.0


def test_settings_can_still_be_disabled_explicitly() -> None:
    from app.config import Settings

    s = Settings(TELEGRAM_DEDUP_COOLDOWN_HOURS=None)
    assert s.TELEGRAM_DEDUP_COOLDOWN_HOURS is None


def test_settings_accepts_positive_value() -> None:
    from app.config import Settings

    s = Settings(TELEGRAM_DEDUP_COOLDOWN_HOURS=6.0)
    assert s.TELEGRAM_DEDUP_COOLDOWN_HOURS == 6.0


@pytest.mark.parametrize("bad_value", [0.0, -1.0, -6.0])
def test_settings_rejects_non_positive_values(bad_value: float) -> None:
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(TELEGRAM_DEDUP_COOLDOWN_HOURS=bad_value)
