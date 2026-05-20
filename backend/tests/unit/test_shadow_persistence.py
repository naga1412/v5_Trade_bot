import pytest
import sqlalchemy as sa
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.shadow.engine import ShadowPosition, ShadowSignal, Direction
from app.shadow.exit_monitor import ExitReason
from app.shadow.persistence import (
    persist_open_position,
    delete_open_position,  # noqa: F401 — part of public API, tested via round-trip
    persist_closed_trade,
    set_cooldown,
    list_open_positions,
    load_cooldowns,
)


def make_signal() -> ShadowSignal:
    return ShadowSignal(
        symbol="BTCUSDT", direction=Direction.LONG, score=0.65,
        confidence=0.72, entry_price=78250.0, stop_loss=77077.75,
        take_profit=80594.5, atr=781.5,
        layer_scores={"1": 0.85, "3": 0.72, "5": 0.40},
        ts=datetime(2026, 5, 3, 14, tzinfo=timezone.utc),
        signal_id="abc12345",
    )


# SP-0.7 Phase E2: per-user shadow tables. The test fixtures match the
# production schema after migrations 0005/0006: user_id NOT NULL on each
# row, shadow_cooldowns keyed by (user_id, symbol).
_OPEN_POS_DDL = (
    "CREATE TABLE shadow_open_positions ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    # PR3 (alembic 0021): timeframe column + (symbol, timeframe) UNIQUE
    "timeframe TEXT NOT NULL DEFAULT '1h', "
    "direction TEXT NOT NULL, "
    "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
    "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
    "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
    "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
    "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
    "signal_id TEXT NOT NULL UNIQUE, "
    "UNIQUE (symbol, timeframe))"
)

_TRADES_DDL = (
    "CREATE TABLE shadow_trades ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    "timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
    "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
    "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
    "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
    "layer_scores TEXT NOT NULL, entry_atr REAL NOT NULL, "
    "exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_usdt REAL, "
    "bars_held INTEGER, opened_at TEXT NOT NULL, closed_at TEXT, "
    "inputs_hash TEXT NOT NULL, model_version TEXT NOT NULL, "
    "signal_id TEXT NOT NULL UNIQUE, "
    # PR3 G1: recording-only scaling columns (NULL when scaling OFF)
    "hold_scaling_factor REAL, hold_timeout_bars INTEGER, "
    # PR-strategy-1: PR1 analytics columns. NULL when source pred lacked
    # them. NON_HASHED_ALLOW_LIST per app/db/audit.py.
    "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
    "mtf_directions_json TEXT, p_win REAL, effective_score REAL, "
    "realized_vol_20d REAL, funding_directional_adj REAL, "
    "prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
)

_COOLDOWNS_DDL = (
    "CREATE TABLE shadow_cooldowns ("
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    # PR3 (alembic 0021): timeframe in PK
    "timeframe TEXT NOT NULL DEFAULT '1h', "
    "cooldown_until TEXT NOT NULL, "
    "PRIMARY KEY (user_id, symbol, timeframe))"
)


@pytest.mark.asyncio
async def test_persist_and_retrieve_open_position() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))

    sig = make_signal()
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        await session.commit()

        loaded = await list_open_positions(session, user_id=1)
    assert len(loaded) == 1
    assert loaded[0].symbol == "BTCUSDT"
    assert loaded[0].entry_price == 78250.0
    assert loaded[0].signal_id == "abc12345"


@pytest.mark.asyncio
async def test_persist_open_position_writes_user_id() -> None:
    """Spec §7: user_id is the per-row owner; persist_open_position must store it."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))

    sig = make_signal()
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=7)
        await session.commit()
        row = (await session.execute(
            sa.text("SELECT user_id FROM shadow_open_positions")
        )).first()
    assert row is not None
    assert row.user_id == 7


@pytest.mark.asyncio
async def test_list_open_positions_filters_by_user() -> None:
    """Two users have positions on different symbols — list_open_positions must isolate."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_OPEN_POS_DDL))

    pos1 = ShadowPosition.from_signal(make_signal(), position_size_usdt=30.0)
    sig2 = ShadowSignal(
        symbol="ETHUSDT", direction=Direction.LONG, score=0.65,
        confidence=0.72, entry_price=2000.0, stop_loss=1900.0,
        take_profit=2200.0, atr=20.0,
        layer_scores={"1": 0.85, "3": 0.72, "5": 0.40},
        ts=datetime(2026, 5, 3, 14, tzinfo=timezone.utc),
        signal_id="user2-sig",
    )
    pos2 = ShadowPosition.from_signal(sig2, position_size_usdt=30.0)

    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos1, user_id=1)
        await persist_open_position(session, pos2, user_id=2)
        await session.commit()

        u1 = await list_open_positions(session, user_id=1)
        u2 = await list_open_positions(session, user_id=2)
    assert {p.symbol for p in u1} == {"BTCUSDT"}
    assert {p.symbol for p in u2} == {"ETHUSDT"}


@pytest.mark.asyncio
async def test_delete_open_position_filters_by_user() -> None:
    """Deleting user 1's position must NOT touch user 2's row on the same symbol."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # Note: production schema has UNIQUE(symbol) but per the multi-user spec
    # SP-8 will replace it with UNIQUE(user_id, symbol). For now (single-worker
    # bootstrap admin), this test exercises the user_id predicate explicitly
    # using a relaxed schema.
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "  # PR3 alembic 0021
            "direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL, "
            "UNIQUE (user_id, symbol, timeframe))"
        ))

    sig = make_signal()
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    async with AsyncSession(engine) as session:
        await persist_open_position(session, pos, user_id=1)
        # Use a distinct signal_id for user 2 since signal_id was UNIQUE on prod.
        # (The single-user prod schema still enforces signal_id uniqueness.)
        await session.execute(sa.text(
            "INSERT INTO shadow_open_positions "
            "(user_id, symbol, direction, entry_price, stop_loss, take_profit, "
            "position_size_usdt, entry_score, entry_confidence, entry_atr, "
            "bars_held, opened_at, last_check_at, signal_id) "
            "VALUES (2, 'BTCUSDT', 'LONG', 78250.0, 77077.75, 80594.5, 30.0, "
            "0.65, 0.72, 781.5, 0, '2026-05-03T14:00:00+00:00', "
            "'2026-05-03T14:00:00+00:00', 'user2-sig')"
        ))
        await session.commit()

        await delete_open_position(session, user_id=1, symbol="BTCUSDT")
        await session.commit()

        rows = (await session.execute(
            sa.text("SELECT user_id, symbol FROM shadow_open_positions")
        )).all()
    assert len(rows) == 1
    assert rows[0].user_id == 2


@pytest.mark.asyncio
async def test_persist_closed_trade_with_audit_chain() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_TRADES_DDL))

    sig = make_signal()
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    closed_at = datetime(2026, 5, 3, 18, tzinfo=timezone.utc)

    async with AsyncSession(engine) as session:
        row_hash = await persist_closed_trade(
            session, pos, user_id=1,
            exit_price=80594.5, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=closed_at, bars_held=4, inputs_hash="deadbeef",
        )
        await session.commit()
        rows = (await session.execute(
            sa.text("SELECT * FROM shadow_trades")
        )).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.user_id == 1
    assert r.symbol == "BTCUSDT"
    assert r.exit_price == 80594.5
    assert r.exit_reason == "TAKE_PROFIT"
    assert r.pnl_pct == pytest.approx((80594.5 - 78250.0) / 78250.0 * 100)
    assert r.row_hash == row_hash
    assert r.prev_hash == "0" * 64  # genesis hash for first row


@pytest.mark.asyncio
async def test_set_and_check_cooldown() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_COOLDOWNS_DDL))

    until = datetime(2026, 5, 3, 14, 30, tzinfo=timezone.utc)
    async with AsyncSession(engine) as session:
        await set_cooldown(session, user_id=1, symbol="BTCUSDT", until=until)
        await session.commit()
        # Set again — should upsert on (user_id, symbol)
        await set_cooldown(
            session, user_id=1, symbol="BTCUSDT",
            until=until + timedelta(minutes=10),
        )
        await session.commit()
        rows = (await session.execute(
            sa.text("SELECT user_id, symbol, cooldown_until FROM shadow_cooldowns")
        )).all()
    assert len(rows) == 1
    assert rows[0].user_id == 1


@pytest.mark.asyncio
async def test_cooldowns_isolated_per_user() -> None:
    """Two users may hold cooldowns on the same symbol independently."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_COOLDOWNS_DDL))

    until1 = datetime(2026, 5, 3, 14, 30, tzinfo=timezone.utc)
    until2 = datetime(2026, 5, 3, 15, 0, tzinfo=timezone.utc)
    async with AsyncSession(engine) as session:
        await set_cooldown(session, user_id=1, symbol="BTCUSDT", until=until1)
        await set_cooldown(session, user_id=2, symbol="BTCUSDT", until=until2)
        await session.commit()

        u1 = await load_cooldowns(session, user_id=1)
        u2 = await load_cooldowns(session, user_id=2)
    # `load_cooldowns` keeps the legacy {symbol: datetime} shape filtered
    # to timeframe='1h' for backward-compat with the existing shadow_worker.
    # New per-tf code uses `load_cooldowns_per_tf` instead (PR3 Phase 4).
    assert u1 == {"BTCUSDT": until1}
    assert u2 == {"BTCUSDT": until2}


@pytest.mark.asyncio
async def test_persist_closed_trade_writes_all_7_pr1_cols() -> None:
    """PR-strategy-1: when ShadowPosition carries the 7 PR1 fields,
    persist_closed_trade threads them through build_shadow_trade_payload and
    they land in shadow_trades. Round-trip integration on SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(_TRADES_DDL))

    sig = make_signal()
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    # PR-strategy-1: worker populates these from `pred` between
    # from_signal() and persist_open_position(). Simulate that here.
    pos.mtf_agreement = 4
    pos.mtf_dominant_tf = "1h"
    pos.mtf_directions_json = '{"5m":1,"1h":1}'
    pos.p_win = 0.65
    pos.effective_score = 0.42
    pos.realized_vol_20d = 0.018
    pos.funding_directional_adj = 0.003
    closed_at = datetime(2026, 5, 20, 18, tzinfo=timezone.utc)

    async with AsyncSession(engine) as session:
        await persist_closed_trade(
            session, pos, user_id=1,
            exit_price=80594.5, exit_reason=ExitReason.TAKE_PROFIT,
            closed_at=closed_at, bars_held=4, inputs_hash="deadbeef",
        )
        await session.commit()
        row = (await session.execute(
            sa.text(
                "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json, "
                "p_win, effective_score, realized_vol_20d, "
                "funding_directional_adj FROM shadow_trades"
            )
        )).first()
    assert row is not None
    assert row.mtf_agreement == 4
    assert row.mtf_dominant_tf == "1h"
    assert row.mtf_directions_json == '{"5m":1,"1h":1}'
    assert row.p_win == 0.65
    assert row.effective_score == 0.42
    assert row.realized_vol_20d == 0.018
    assert row.funding_directional_adj == 0.003
