"""Unit tests for the prediction accuracy validator (Feature 2).

Three things to cover:
1. ``_classify_correct``: pure function over (direction, anchor, target).
2. ``record_pending_validation``: inserts a pending row with the right
   target_ts.
3. ``validate_pending``: picks up due rows, fetches a close via the
   adapter, updates was_correct + pnl_pct.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ml import validator


@pytest.fixture
async def validations_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE prediction_validations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "prediction_id INTEGER, user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "direction TEXT NOT NULL, score REAL NOT NULL, "
            "confidence REAL NOT NULL, "
            "anchor_ts TIMESTAMP NOT NULL, anchor_close REAL NOT NULL, "
            "target_ts TIMESTAMP NOT NULL, target_close REAL, "
            "was_correct BOOLEAN, pnl_pct REAL, validated_at TIMESTAMP, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
    yield factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# _classify_correct
# ---------------------------------------------------------------------------


def test_classify_long_correct_when_target_above_anchor() -> None:
    correct, pnl = validator._classify_correct("LONG", 100.0, 102.0)
    assert correct is True
    assert pnl == pytest.approx(2.0)


def test_classify_long_wrong_when_target_below_anchor() -> None:
    correct, pnl = validator._classify_correct("LONG", 100.0, 98.0)
    assert correct is False
    assert pnl == pytest.approx(-2.0)


def test_classify_short_correct_when_target_below_anchor() -> None:
    correct, pnl = validator._classify_correct("SHORT", 100.0, 97.0)
    assert correct is True
    # SHORT pnl is the inverse of LONG move
    assert pnl == pytest.approx(3.0)


def test_classify_short_wrong_when_target_above_anchor() -> None:
    correct, pnl = validator._classify_correct("SHORT", 100.0, 103.0)
    assert correct is False
    assert pnl == pytest.approx(-3.0)


def test_classify_neutral_correct_when_inside_epsilon() -> None:
    # 0.05% move, well under the default 0.1% epsilon → NEUTRAL is right.
    correct, pnl = validator._classify_correct("NEUTRAL", 100.0, 100.05)
    assert correct is True
    assert abs(pnl) < 0.1


def test_classify_neutral_wrong_when_move_exceeds_epsilon() -> None:
    correct, pnl = validator._classify_correct("NEUTRAL", 100.0, 100.5)
    assert correct is False
    assert pnl == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# record_pending_validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_pending_validation_inserts_row_with_correct_target_ts(
    validations_factory,
) -> None:
    factory = validations_factory
    anchor = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    async with factory() as session:
        await validator.record_pending_validation(
            session,
            prediction_id=None, user_id=1,
            symbol="BTC/USDT", timeframe="1h", direction="LONG",
            score=0.4, confidence=0.7,
            anchor_ts=anchor, anchor_close=81000.0,
        )
        await session.commit()
        rows = (await session.execute(sa.text(
            "SELECT direction, anchor_close, anchor_ts, target_ts, validated_at "
            "FROM prediction_validations"
        ))).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.direction == "LONG"
    assert float(r.anchor_close) == 81000.0
    # target_ts is anchor + 1 timeframe (1h = 3600s). SQLite formats
    # datetimes with a space (no T), so compare on the YYYY-MM-DD HH prefix.
    expected_anchor_prefix = anchor.strftime("%Y-%m-%d %H:%M")
    expected_target_prefix = (anchor + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    assert expected_anchor_prefix in str(r.anchor_ts)
    assert expected_target_prefix in str(r.target_ts)
    assert r.validated_at is None


# ---------------------------------------------------------------------------
# validate_pending — picks up due rows + updates them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_pending_marks_row_correct_and_updates_pnl(
    validations_factory,
) -> None:
    factory = validations_factory
    anchor = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    target = anchor + timedelta(hours=1)

    async with factory() as session:
        await validator.record_pending_validation(
            session,
            prediction_id=None, user_id=1,
            symbol="BTC/USDT", timeframe="1h", direction="LONG",
            score=0.4, confidence=0.7,
            anchor_ts=anchor, anchor_close=81000.0,
        )
        await session.commit()

    async def _fake_fetch_close(_http: Any, _sym: str, _tf: str, _ts: datetime) -> float:
        return 82000.0  # +1.23% move → LONG was correct

    with patch.object(validator, "_fetch_close_at", _fake_fetch_close):
        # Use a far-future "now" so the row's target_ts <= now is True.
        n = await validator.validate_pending(
            factory, http=object(),  # type: ignore[arg-type] — bypass close
            now=target + timedelta(minutes=5),
        )
    assert n == 1

    async with factory() as session:
        r = (await session.execute(sa.text(
            "SELECT was_correct, pnl_pct, target_close, validated_at "
            "FROM prediction_validations"
        ))).first()
    assert r is not None
    assert bool(r.was_correct) is True
    assert float(r.pnl_pct) == pytest.approx(1.2345679, rel=1e-3)
    assert float(r.target_close) == 82000.0
    assert r.validated_at is not None


@pytest.mark.asyncio
async def test_validate_pending_skips_rows_not_yet_due(
    validations_factory,
) -> None:
    factory = validations_factory
    anchor = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)

    async with factory() as session:
        await validator.record_pending_validation(
            session,
            prediction_id=None, user_id=1,
            symbol="BTC/USDT", timeframe="1h", direction="LONG",
            score=0.4, confidence=0.7,
            anchor_ts=anchor, anchor_close=81000.0,
        )
        await session.commit()

    async def _should_not_be_called(*_a: Any, **_kw: Any) -> float:
        raise AssertionError("fetch_close_at must not run for not-yet-due rows")

    with patch.object(validator, "_fetch_close_at", _should_not_be_called):
        # "now" is BEFORE the target_ts (anchor + 1h) — row not due yet.
        n = await validator.validate_pending(
            factory, http=object(),  # type: ignore[arg-type]
            now=anchor + timedelta(minutes=30),
        )
    assert n == 0


@pytest.mark.asyncio
async def test_validate_pending_swallows_close_fetch_failure(
    validations_factory,
) -> None:
    """A failed kline fetch must NOT block the rest of the batch."""
    factory = validations_factory
    anchor = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    target = anchor + timedelta(hours=1)

    async with factory() as session:
        for sym in ("BTC/USDT", "ETH/USDT"):
            await validator.record_pending_validation(
                session,
                prediction_id=None, user_id=1,
                symbol=sym, timeframe="1h", direction="LONG",
                score=0.4, confidence=0.7,
                anchor_ts=anchor, anchor_close=100.0,
            )
        await session.commit()

    async def _flaky_fetch(_http: Any, sym: str, _tf: str, _ts: datetime) -> float | None:
        if sym == "BTC/USDT":
            return None  # simulate fetch failure
        return 105.0  # ETH succeeds

    with patch.object(validator, "_fetch_close_at", _flaky_fetch):
        n = await validator.validate_pending(
            factory, http=object(),  # type: ignore[arg-type]
            now=target + timedelta(minutes=5),
        )
    # Only ETH got validated; BTC stays pending.
    assert n == 1
