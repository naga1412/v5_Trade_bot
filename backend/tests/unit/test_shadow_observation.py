"""Tests for app/shadow/observation.py — RL obs-components capture.

Covers the pure assembler (no I/O), the macro-from-ts derivation, and
the async DB helpers against a SQLite in-memory mirror of the
shadow_observations + intermarket_snapshots tables.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shadow.observation import (
    SCHEMA_VERSION,
    REGIME_MAPPING,
    _build_components,
    _macro_from_ts,
    build_obs_components,
    load_observation_components,
    persist_observation,
)


@pytest.fixture(autouse=True)
def _mock_regime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent real Binance HTTP fetch in unit tests — regime always 'neutral'."""
    async def _neutral():
        return "neutral"
    monkeypatch.setattr("app.shadow.observation.get_cached_market_regime", _neutral)


CREATE_INTERMARKET = (
    "CREATE TABLE intermarket_snapshots ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
    "captured_at TEXT NOT NULL DEFAULT (datetime('now')), "
    "funding_rate REAL, mark_price REAL, open_interest REAL, source TEXT NOT NULL)"
)

CREATE_OBS = (
    "CREATE TABLE shadow_observations ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "signal_id TEXT NOT NULL UNIQUE, "
    "user_id INTEGER NOT NULL, "
    "symbol TEXT NOT NULL, "
    "opened_at TEXT NOT NULL, "
    "components TEXT NOT NULL, "
    "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(CREATE_INTERMARKET))
        await conn.execute(sa.text(CREATE_OBS))
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# Macro derivation — pure function, no DB.


def test_macro_weekend_detection() -> None:
    # 2026-05-16 is a Saturday.
    sat = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    m = _macro_from_ts(sat)
    assert m["weekend"] is True

    # 2026-05-15 is a Friday.
    fri = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    assert _macro_from_ts(fri)["weekend"] is False


def test_macro_asia_open_window() -> None:
    asia_morning = datetime(2026, 5, 15, 2, 0, tzinfo=timezone.utc)
    assert _macro_from_ts(asia_morning)["asia_open"] is True

    us_morning = datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc)
    assert _macro_from_ts(us_morning)["asia_open"] is False


def test_macro_naive_datetime_treated_as_utc() -> None:
    naive = datetime(2026, 5, 16, 3, 0)  # Saturday 03:00 UTC if interpreted as UTC
    m = _macro_from_ts(naive)
    assert m["weekend"] is True
    assert m["asia_open"] is True


# ---------------------------------------------------------------------------
# _build_components — pure assembler.


def test_components_carries_layer_scores_intact() -> None:
    ls = [0.1, -0.2, 0.3, -0.4, 0.5, 0.0, 0.0, 0.7, -0.1]
    comp = _build_components(
        symbol="ETHUSDT",
        captured_at=datetime(2026, 5, 15, 6, 0, tzinfo=timezone.utc),
        atr=22.5, last_close=2245.41,
        layer_scores_array=ls,
        intermarket={"funding_rate": 0.0001, "open_interest": 1.5e9},
    )
    assert comp["layer_scores"] == ls
    assert comp["schema_version"] == SCHEMA_VERSION
    assert comp["symbol"] == "ETHUSDT"


def test_components_atr_pct_computed_correctly() -> None:
    comp = _build_components(
        symbol="ETHUSDT",
        captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=22.5, last_close=2245.41,
        layer_scores_array=[0.0] * 9,
        intermarket=None,
    )
    assert comp["market"]["atr_pct"] == pytest.approx(22.5 / 2245.41, abs=1e-9)


def test_components_atr_pct_zero_when_last_close_zero() -> None:
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=22.5, last_close=0.0,
        layer_scores_array=[0.0] * 9,
        intermarket=None,
    )
    assert comp["market"]["atr_pct"] == 0.0


def test_components_no_intermarket_yields_none_funding() -> None:
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9, intermarket=None,
    )
    assert comp["market"]["funding_rate"] is None
    assert comp["market"]["open_interest"] is None


def test_components_oi_delta_propagates_from_intermarket() -> None:
    """oi_delta_24h from intermarket dict is stored in output unchanged."""
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9,
        intermarket={"funding_rate": 0.0001, "open_interest": 1.0e9, "oi_delta_24h": 0.15},
    )
    assert comp["market"]["oi_delta_24h"] == pytest.approx(0.15)


def test_components_oi_delta_none_when_intermarket_absent() -> None:
    """oi_delta_24h is None when no intermarket row exists."""
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9, intermarket=None,
    )
    assert comp["market"]["oi_delta_24h"] is None


def test_components_oi_delta_none_when_not_in_intermarket_dict() -> None:
    """oi_delta_24h is None when intermarket exists but has no oi_delta_24h key."""
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9,
        intermarket={"funding_rate": 0.0001, "open_interest": 1.0e9},
    )
    assert comp["market"]["oi_delta_24h"] is None


def test_components_position_state_zeros_at_open() -> None:
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9, intermarket=None,
    )
    pos = comp["position"]
    assert pos == {"cur_position": 0, "unrealized_pnl_R": 0.0, "bars_in_position": 0}


# ---------------------------------------------------------------------------
# Async build_obs_components — exercises the intermarket lookup.


@pytest.mark.asyncio
async def test_build_obs_components_pulls_latest_intermarket(
    session: AsyncSession,
) -> None:
    # Insert two snapshots, the second more recent.
    await session.execute(sa.text("""
        INSERT INTO intermarket_snapshots
        (symbol, captured_at, funding_rate, open_interest, source)
        VALUES ('ETHUSDT', '2026-05-15T05:00:00+00:00', 0.0001, 1.0e9, 'binance_futures')
    """))
    await session.execute(sa.text("""
        INSERT INTO intermarket_snapshots
        (symbol, captured_at, funding_rate, open_interest, source)
        VALUES ('ETHUSDT', '2026-05-15T06:00:00+00:00', 0.0008, 2.0e9, 'binance_futures')
    """))
    await session.commit()

    comp = await build_obs_components(
        session,
        symbol="ETHUSDT",
        captured_at=datetime(2026, 5, 15, 6, 5, tzinfo=timezone.utc),
        atr=22.5, last_close=2245.41,
        layer_scores_array=[0.0] * 9,
    )
    assert comp["market"]["funding_rate"] == pytest.approx(0.0008)
    assert comp["market"]["open_interest"] == pytest.approx(2.0e9)


@pytest.mark.asyncio
async def test_build_obs_components_no_intermarket_row_safe(
    session: AsyncSession,
) -> None:
    """If no snapshot exists for the symbol, the helper still returns a
    valid components dict (funding=None, open_interest=None)."""
    comp = await build_obs_components(
        session,
        symbol="BRANDNEWCOIN", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9,
    )
    assert comp["market"]["funding_rate"] is None


@pytest.mark.asyncio
async def test_oi_delta_24h_computed_from_25h_apart_rows(
    session: AsyncSession,
) -> None:
    """oi_delta_24h matches predictor formula: (latest_OI - baseline_OI) / baseline_OI.

    Uses bound-parameter INSERTs so SQLite stores datetimes in a format
    consistent with the baseline-query binding.
    """
    baseline_ts = datetime(2026, 5, 13, 6, 0, tzinfo=timezone.utc)
    latest_ts = datetime(2026, 5, 14, 7, 0, tzinfo=timezone.utc)  # 25h later
    insert_sql = sa.text(
        "INSERT INTO intermarket_snapshots "
        "(symbol, captured_at, funding_rate, open_interest, source) "
        "VALUES (:sym, :ts, :fr, :oi, 'test')"
    )
    await session.execute(insert_sql, {"sym": "SOLUSDT", "ts": baseline_ts, "fr": 0.0001, "oi": 1.0e9})
    await session.execute(insert_sql, {"sym": "SOLUSDT", "ts": latest_ts, "fr": 0.0002, "oi": 1.2e9})
    await session.commit()

    comp = await build_obs_components(
        session,
        symbol="SOLUSDT",
        captured_at=latest_ts,
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9,
    )
    expected_delta = (1.2e9 - 1.0e9) / 1.0e9  # 0.2
    assert comp["market"]["oi_delta_24h"] == pytest.approx(expected_delta)


@pytest.mark.asyncio
async def test_oi_delta_24h_none_when_no_baseline_row(
    session: AsyncSession,
) -> None:
    """oi_delta_24h is None when no intermarket row exists 24h before the latest."""
    single_ts = datetime(2026, 5, 14, 7, 0, tzinfo=timezone.utc)
    await session.execute(
        sa.text(
            "INSERT INTO intermarket_snapshots "
            "(symbol, captured_at, funding_rate, open_interest, source) "
            "VALUES (:sym, :ts, :fr, :oi, 'test')"
        ),
        {"sym": "AVAXUSDT", "ts": single_ts, "fr": 0.0003, "oi": 5.0e8},
    )
    await session.commit()

    comp = await build_obs_components(
        session,
        symbol="AVAXUSDT",
        captured_at=single_ts,
        atr=1.0, last_close=50.0,
        layer_scores_array=[0.0] * 9,
    )
    assert comp["market"]["oi_delta_24h"] is None


# ---------------------------------------------------------------------------
# persist + load round-trip.


@pytest.mark.asyncio
async def test_persist_then_load_round_trips_components(
    session: AsyncSession,
) -> None:
    comp_in = {
        "schema_version": 1,
        "captured_at": "2026-05-15T06:00:00+00:00",
        "symbol": "ETHUSDT",
        "atr": 22.5,
        "last_close": 2245.41,
        "layer_scores": [0.1, -0.2, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "market": {
            "atr_pct": 0.01, "funding_rate": 0.0008,
            "open_interest": 2.0e9, "oi_delta_24h": 0.0,
            "dxy_corr_30d": -0.4, "gold_corr_30d": 0.5,
            "regime": "bear_crash",
        },
        "position": {"cur_position": 0, "unrealized_pnl_R": 0.0, "bars_in_position": 0},
        "macro": {
            "hours_to_next_high_impact": 24.0, "fomc_window": False,
            "weekend": False, "asia_open": True,
        },
    }
    await persist_observation(
        session, signal_id="sig-rt-1", user_id=1, symbol="ETHUSDT",
        opened_at=datetime(2026, 5, 15, 6, 0, tzinfo=timezone.utc),
        components=comp_in,
    )
    await session.commit()
    out = await load_observation_components(session, "sig-rt-1")
    assert out is not None
    assert out == comp_in


@pytest.mark.asyncio
async def test_load_missing_signal_id_returns_none(
    session: AsyncSession,
) -> None:
    out = await load_observation_components(session, "nonexistent")
    assert out is None


@pytest.mark.asyncio
async def test_persist_swallows_db_errors_silently(
    session: AsyncSession,
) -> None:
    """persist_observation must not raise on db errors — obs capture is
    best-effort and must never crash the worker."""
    # Duplicate signal_id violates UNIQUE constraint. Helper should log+swallow.
    await persist_observation(
        session, signal_id="sig-dup", user_id=1, symbol="X",
        opened_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        components={"schema_version": 1},
    )
    await session.commit()
    # Second insert with same signal_id — must not propagate the integrity error.
    await persist_observation(
        session, signal_id="sig-dup", user_id=1, symbol="X",
        opened_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        components={"schema_version": 1},
    )
    # Reaching here means no exception escaped — that's the contract.


# ---------------------------------------------------------------------------
# Serialization invariants.


def test_components_is_json_serializable() -> None:
    """The whole dict must round-trip through json.dumps/loads."""
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=22.5, last_close=2245.41,
        layer_scores_array=[0.1, -0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        intermarket={"funding_rate": 0.0001, "open_interest": 1.5e9},
    )
    serialized = json.dumps(comp)
    parsed = json.loads(serialized)
    assert parsed["symbol"] == "X"
    assert parsed["market"]["funding_rate"] == pytest.approx(0.0001)


# ---------------------------------------------------------------------------
# Phase 3B: regime detection wiring.


def test_build_components_default_regime_is_sideways_grind() -> None:
    comp = _build_components(
        symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1.0, last_close=100.0,
        layer_scores_array=[0.0] * 9,
        intermarket=None,
    )
    assert comp["market"]["regime"] == "sideways_grind"


def test_build_components_explicit_regime_passed_through() -> None:
    for raw, expected in REGIME_MAPPING.items():
        if raw is None:
            continue  # None can't be passed as regime; it maps to default
        comp = _build_components(
            symbol="X", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
            atr=1.0, last_close=100.0,
            layer_scores_array=[0.0] * 9,
            intermarket=None,
            regime=expected,
        )
        assert comp["market"]["regime"] == expected


def test_regime_mapping_covers_all_classifier_outputs() -> None:
    for key in ("bull", "bear", "neutral", None):
        assert key in REGIME_MAPPING
    assert REGIME_MAPPING["bull"] == "bull_breakout"
    assert REGIME_MAPPING["bear"] == "bear_crash"
    assert REGIME_MAPPING["neutral"] == "sideways_grind"
    assert REGIME_MAPPING[None] == "sideways_grind"


@pytest.mark.asyncio
async def test_build_obs_components_bull_regime_maps_to_bull_breakout(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_obs_components forwards regime through REGIME_MAPPING."""
    async def _bull():
        return "bull"
    monkeypatch.setattr("app.shadow.observation.get_cached_market_regime", _bull)
    comp = await build_obs_components(
        session,
        symbol="BTCUSDT", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1000.0, last_close=60000.0,
        layer_scores_array=[0.0] * 9,
    )
    assert comp["market"]["regime"] == "bull_breakout"


@pytest.mark.asyncio
async def test_build_obs_components_bear_regime_maps_to_bear_crash(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _bear():
        return "bear"
    monkeypatch.setattr("app.shadow.observation.get_cached_market_regime", _bear)
    comp = await build_obs_components(
        session,
        symbol="BTCUSDT", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1000.0, last_close=60000.0,
        layer_scores_array=[0.0] * 9,
    )
    assert comp["market"]["regime"] == "bear_crash"


@pytest.mark.asyncio
async def test_build_obs_components_none_regime_maps_to_sideways(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch failure (returns None) must fall back to sideways_grind."""
    async def _fail():
        return None
    monkeypatch.setattr("app.shadow.observation.get_cached_market_regime", _fail)
    comp = await build_obs_components(
        session,
        symbol="BTCUSDT", captured_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        atr=1000.0, last_close=60000.0,
        layer_scores_array=[0.0] * 9,
    )
    assert comp["market"]["regime"] == "sideways_grind"
