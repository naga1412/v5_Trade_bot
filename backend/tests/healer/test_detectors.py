"""Healer Phase 0 detectors — pure-logic tests against an in-memory SQLite.

The detectors are ``async`` functions taking a session factory; each test
here sets up a per-detector schema fixture, seeds the exact row shape
that should (or should not) trigger the detector, and asserts on the
returned :class:`HealerFinding` list.

Not covered here (belongs to the runner tests): finding persistence,
alert_admin fan-out, worker heartbeats. Those are integration-shaped.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.healer import detectors as detectors_mod
from app.healer.detectors import (
    detect_blocked_rate_anomaly,
    detect_dispatch_error_rate,
    detect_per_symbol_prediction_freshness,
    detect_score_distribution_anomaly,
)
from app.healer.findings import DISPATCH_EXCEPTION_DETECTOR


@pytest.fixture
def bypass_c3_boot_grace(monkeypatch):
    """Force C3 to skip the boot-grace check so tests can exercise the
    detector body without waiting real wall-clock time. The Phase 0
    completion adds a 1h+5min grace at module load; tests need to opt in."""
    monkeypatch.setattr(
        detectors_mod, "_container_uptime_seconds", lambda: 999_999.0,
    )


@pytest.fixture(autouse=True)
def _reset_c3_universe_stall_streak():
    """Wipe the in-memory C3 universe-stall streak between tests so a
    prior test's streak can't cascade into an unrelated test's assertion."""
    detectors_mod._C3_UNIVERSE_STALL_STREAK.clear()
    yield
    detectors_mod._C3_UNIVERSE_STALL_STREAK.clear()


@pytest.fixture(autouse=True)
def _reset_c3_per_symbol_drop_dedup():
    """Wipe the in-memory per-symbol-drop dedup state between tests."""
    detectors_mod._C3_LAST_PER_SYMBOL_DROP.clear()
    yield
    detectors_mod._C3_LAST_PER_SYMBOL_DROP.clear()


# ---- fixtures -----------------------------------------------------------


async def _mk_healer_findings_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # SQLite stand-in for healer_findings — no JSONB, but the
        # detector's ``details->>'exception_type'`` idiom is exercised
        # via a JSON1 workaround in the query. To keep the test suite
        # dialect-portable we use TEXT for details + query
        # json_extract to peek at the exception_type.
        await conn.execute(sa.text(
            "CREATE TABLE healer_findings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "detector_name TEXT NOT NULL, "
            "detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "severity TEXT NOT NULL, "
            "summary TEXT NOT NULL, "
            "details TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE healer_known_error_types ("
            "error_type TEXT PRIMARY KEY, "
            "first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "seen_count INTEGER NOT NULL DEFAULT 1)"
        ))
    return engine


async def _mk_predictions_engine(*, seed_universe: list[str] | None = None):
    """SQLite in-memory fixture for C3 tests.

    ``seed_universe`` (Binance no-slash form, e.g. ``["BTCUSDT",
    "ETHUSDT"]``) seeds one row per symbol in asset_universe with
    snapshot_at=NOW. C3 requires a non-empty current universe now
    (universe-scope fix 2026-07-24); pass an explicit list unless the
    test is exercising the empty-universe guard.

    Note: for tests that seed predictions with slash-form symbols like
    'SYM0/USDT', pass the corresponding no-slash forms 'SYM0USDT' here.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "ts TEXT NOT NULL, "
            "direction TEXT NOT NULL, "
            "final_score REAL NOT NULL, "
            "confidence REAL NOT NULL)"
        ))
        # worker_heartbeats — needed by C3's _shadow_worker_is_fresh
        # helper to decide universe-stall vs per-symbol-drop.
        await conn.execute(sa.text(
            "CREATE TABLE worker_heartbeats ("
            "worker_name TEXT PRIMARY KEY, "
            "beat_at TEXT NOT NULL, "
            "last_status TEXT, "
            "details TEXT)"
        ))
        # asset_universe — needed by C3's universe-scope filter (added
        # 2026-07-24). Same column names as prod's SP-3.5 schema.
        await conn.execute(sa.text(
            "CREATE TABLE asset_universe ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "snapshot_at TEXT NOT NULL, "
            "quote_volume_usd_24h REAL, "
            "rank INTEGER)"
        ))
        if seed_universe:
            now_iso = datetime.now(timezone.utc).isoformat()
            for i, sym in enumerate(seed_universe):
                await conn.execute(sa.text(
                    "INSERT INTO asset_universe "
                    "(symbol, snapshot_at, quote_volume_usd_24h, rank) "
                    "VALUES (:s, :t, 0, :r)"
                ), {"s": sym, "t": now_iso, "r": i})
    return engine


async def _mk_telegram_signals_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, "
            "sent_at TEXT NOT NULL, "
            "response TEXT)"
        ))
    return engine


# ---- C1 tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_c1_novel_exception_type_alarms_critical() -> None:
    """First time an exception_type appears → critical alarm."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        await s.execute(sa.text(
            "INSERT INTO healer_findings "
            "(detector_name, detected_at, severity, summary, details) "
            "VALUES (:d, :t, 'warning', 'x', :j)"
        ), {
            "d": DISPATCH_EXCEPTION_DETECTOR,
            "t": (now - timedelta(minutes=5)).isoformat(),
            "j": json.dumps({"exception_type": "AsyncpgDataError"}),
        })
        await s.commit()

    findings = await detect_dispatch_error_rate(factory)
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "NEVER-BEFORE-SEEN" in findings[0].summary
    assert findings[0].details is not None
    assert findings[0].details["exception_type"] == "AsyncpgDataError"


@pytest.mark.asyncio
async def test_c1_known_type_below_rate_limit_no_finding() -> None:
    """Known type + <=5 hits → no finding."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(sa.text(
            "INSERT INTO healer_known_error_types "
            "(error_type, first_seen_at, last_seen_at, seen_count) "
            "VALUES ('BinanceLiveError', '2026-07-01', '2026-07-01', 42)"
        ))
        await s.commit()
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for _ in range(3):
            await s.execute(sa.text(
                "INSERT INTO healer_findings "
                "(detector_name, detected_at, severity, summary, details) "
                "VALUES (:d, :t, 'warning', 'x', :j)"
            ), {
                "d": DISPATCH_EXCEPTION_DETECTOR,
                "t": (now - timedelta(minutes=5)).isoformat(),
                "j": json.dumps({"exception_type": "BinanceLiveError"}),
            })
        await s.commit()

    findings = await detect_dispatch_error_rate(factory)
    assert findings == []


@pytest.mark.asyncio
async def test_c1_known_type_above_rate_limit_alarms_warning() -> None:
    """Known type + >5 hits/hr → warning alarm."""
    engine = await _mk_healer_findings_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(sa.text(
            "INSERT INTO healer_known_error_types "
            "(error_type, first_seen_at, last_seen_at, seen_count) "
            "VALUES ('BinanceLiveError', '2026-07-01', '2026-07-01', 42)"
        ))
        await s.commit()
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for _ in range(7):
            await s.execute(sa.text(
                "INSERT INTO healer_findings "
                "(detector_name, detected_at, severity, summary, details) "
                "VALUES (:d, :t, 'warning', 'x', :j)"
            ), {
                "d": DISPATCH_EXCEPTION_DETECTOR,
                "t": (now - timedelta(minutes=5)).isoformat(),
                "j": json.dumps({"exception_type": "BinanceLiveError"}),
            })
        await s.commit()

    findings = await detect_dispatch_error_rate(factory)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "hit 7 times" in findings[0].summary


# ---- C2 tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_c2_all_neutral_universe_alarms_critical() -> None:
    """Every prediction over the lookback window is NEUTRAL → critical."""
    engine = await _mk_predictions_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(50):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'NEUTRAL', 0.0, 0.5)"
            ), {
                "sym": f"SYM{i:03d}/USDT",
                "t": (now - timedelta(minutes=5)).isoformat(),
            })
        await s.commit()

    findings = await detect_score_distribution_anomaly(factory)
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "predictor degradation" in findings[0].summary


@pytest.mark.asyncio
async def test_c2_healthy_score_mix_no_finding() -> None:
    """Realistic mix of LONG / SHORT / NEUTRAL → no finding."""
    engine = await _mk_predictions_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        rows = [
            ("SYM001/USDT", "LONG", 0.35),
            ("SYM002/USDT", "SHORT", -0.42),
            ("SYM003/USDT", "NEUTRAL", 0.02),
        ]
        for _ in range(15):  # 45 rows > 30 sample-size floor
            for sym, direction, score in rows:
                await s.execute(sa.text(
                    "INSERT INTO predictions "
                    "(symbol, timeframe, ts, direction, final_score, "
                    " confidence) "
                    "VALUES (:sym, '1h', :t, :d, :s, 0.6)"
                ), {
                    "sym": sym, "d": direction, "s": score,
                    "t": (now - timedelta(minutes=5)).isoformat(),
                })
        await s.commit()

    findings = await detect_score_distribution_anomaly(factory)
    assert findings == []


@pytest.mark.asyncio
async def test_c2_below_sample_size_floor_no_finding() -> None:
    """Too few predictions in the window → other detectors handle it."""
    engine = await _mk_predictions_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(5):  # < 30 sample-size floor
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'NEUTRAL', 0.0, 0.5)"
            ), {
                "sym": f"SYM{i}/USDT",
                "t": (now - timedelta(minutes=5)).isoformat(),
            })
        await s.commit()

    findings = await detect_score_distribution_anomaly(factory)
    assert findings == []


# ---- C3 tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_c3_stale_symbol_alarms_warning(bypass_c3_boot_grace) -> None:
    """SUBSET stale (per-symbol drop) → warning. Original C3 value."""
    engine = await _mk_predictions_engine(
        seed_universe=["FRESHUSDT", "STALEUSDT"],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # Fresh: 30-min old on 1h TF → fresh
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('FRESH/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(minutes=30)).isoformat()})
        # Stale: 3-hr old on 1h TF → stale (>2h)
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('STALE/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(hours=3)).isoformat()})
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].details is not None
    assert findings[0].details["stale_count"] == 1
    assert findings[0].details["class"] == "per_symbol_drop"
    sample = findings[0].details["sample"]
    assert any(item["symbol"] == "STALE/USDT" for item in sample)


async def _seed_one_stale_symbol(
    *, fresh_symbol: str, stale_symbol: str,
) -> async_sessionmaker:
    """Shared fixture for the dedup tests below — one fresh, one stale,
    identical shape to test_c3_stale_symbol_alarms_warning."""
    engine = await _mk_predictions_engine(
        seed_universe=[fresh_symbol.replace("/", ""), stale_symbol.replace("/", "")],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"sym": fresh_symbol, "t": (now - timedelta(minutes=30)).isoformat()})
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"sym": stale_symbol, "t": (now - timedelta(hours=3)).isoformat()})
        await s.commit()
    return factory


@pytest.mark.asyncio
async def test_c3_per_symbol_drop_suppresses_identical_repeat_tick(
    bypass_c3_boot_grace,
) -> None:
    """Alert-fatigue fix (2026-08-08): live confirmed ACE/BABY/BICO/ERA
    USDT/1h produced an unchanged per_symbol_drop warning every 5 minutes
    for weeks (BABYUSDT: 50+ days stale, unbroken). Back-to-back ticks
    with an IDENTICAL stale set must not both emit."""
    factory = await _seed_one_stale_symbol(
        fresh_symbol="FRESH/USDT", stale_symbol="STALE/USDT",
    )
    first = await detect_per_symbol_prediction_freshness(factory)
    assert len(first) == 1
    assert first[0].details["class"] == "per_symbol_drop"

    second = await detect_per_symbol_prediction_freshness(factory)
    assert second == []


@pytest.mark.asyncio
async def test_c3_per_symbol_drop_reemits_when_stale_set_changes(
    bypass_c3_boot_grace,
) -> None:
    """A genuinely NEW drop (different symbol) must still surface even
    though a per_symbol_drop finding was already emitted this session —
    dedup keys on the SET of stale pairs, not just "was there a finding
    before."""
    factory = await _seed_one_stale_symbol(
        fresh_symbol="FRESH/USDT", stale_symbol="STALE/USDT",
    )
    first = await detect_per_symbol_prediction_freshness(factory)
    assert len(first) == 1

    factory2 = await _seed_one_stale_symbol(
        fresh_symbol="FRESH/USDT", stale_symbol="OTHERSTALE/USDT",
    )
    second = await detect_per_symbol_prediction_freshness(factory2)
    assert len(second) == 1
    sample = second[0].details["sample"]
    assert any(item["symbol"] == "OTHERSTALE/USDT" for item in sample)


@pytest.mark.asyncio
async def test_c3_per_symbol_drop_reemits_after_reminder_interval(
    bypass_c3_boot_grace,
) -> None:
    """An UNCHANGED stale set must still get a periodic reminder rather
    than going permanently silent — otherwise a real, never-fixed drop
    (like the live BABYUSDT case) could vanish from operator attention
    entirely instead of just being throttled."""
    factory = await _seed_one_stale_symbol(
        fresh_symbol="FRESH/USDT", stale_symbol="STALE/USDT",
    )
    first = await detect_per_symbol_prediction_freshness(factory)
    assert len(first) == 1

    # Simulate the reminder interval having elapsed since the last emit.
    key = "per_symbol_drop"
    stale_set, _last_emitted = detectors_mod._C3_LAST_PER_SYMBOL_DROP[key]
    detectors_mod._C3_LAST_PER_SYMBOL_DROP[key] = (
        stale_set,
        datetime.now(timezone.utc) - timedelta(
            seconds=detectors_mod.C3_PER_SYMBOL_DROP_REMINDER_SECONDS + 1,
        ),
    )

    second = await detect_per_symbol_prediction_freshness(factory)
    assert len(second) == 1
    assert second[0].details["class"] == "per_symbol_drop"


@pytest.mark.asyncio
async def test_c3_all_symbols_fresh_no_finding(bypass_c3_boot_grace) -> None:
    universe = [f"SYM{i}USDT" for i in range(10)]
    engine = await _mk_predictions_engine(seed_universe=universe)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(10):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"SYM{i}/USDT",
                "t": (now - timedelta(minutes=30)).isoformat(),
            })
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert findings == []


@pytest.mark.asyncio
async def test_c3_boot_grace_suppresses_all_findings(monkeypatch) -> None:
    """During boot grace, C3 returns [] even for definitely-stale rows.

    Regression against the 2026-07-24 prod-promotion #27 first tick that
    emitted 85 warnings because every prediction was pre-boot vintage.
    """
    monkeypatch.setattr(
        detectors_mod, "_container_uptime_seconds", lambda: 60.0,
    )
    engine = await _mk_predictions_engine(
        seed_universe=[f"OLD{i}USDT" for i in range(5)],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(5):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"OLD{i}/USDT",
                "t": (now - timedelta(hours=6)).isoformat(),
            })
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert findings == []


@pytest.mark.asyncio
async def test_c3_universe_stall_first_tick_is_warning_not_critical(
    bypass_c3_boot_grace,
) -> None:
    """Streak gate: FIRST universe_stall observation emits WARNING,
    not CRITICAL. Guards against transient post-restart timing races
    (boot grace expiring seconds after the last hourly close aged past
    2× threshold) paging the operator + resetting the Phase 1 clock."""
    engine = await _mk_predictions_engine(
        seed_universe=[f"SYM{i}USDT" for i in range(5)],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(5):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"SYM{i}/USDT",
                "t": (now - timedelta(hours=6)).isoformat(),
            })
        await s.execute(sa.text(
            "INSERT INTO worker_heartbeats "
            "(worker_name, beat_at, last_status) "
            "VALUES ('shadow_worker', :b, 'ok')"
        ), {"b": (now - timedelta(seconds=30)).isoformat()})
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert len(findings) == 1
    assert findings[0].severity == "warning", (
        f"first-tick universe_stall must be warning, not critical; got "
        f"{findings[0].severity}: {findings[0].summary}"
    )
    assert findings[0].details["class"] == "universe_stall_pending"
    assert findings[0].details["streak"] == 1
    assert findings[0].details["streak_threshold"] == 2


@pytest.mark.asyncio
async def test_c3_universe_stall_escalates_to_critical_on_second_tick(
    bypass_c3_boot_grace,
) -> None:
    """The N=2 streak crossing: two consecutive ticks with the universe
    stall condition holding → CRITICAL on tick 2."""
    engine = await _mk_predictions_engine(
        seed_universe=[f"SYM{i}USDT" for i in range(5)],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(5):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"SYM{i}/USDT",
                "t": (now - timedelta(hours=6)).isoformat(),
            })
        await s.execute(sa.text(
            "INSERT INTO worker_heartbeats "
            "(worker_name, beat_at, last_status) "
            "VALUES ('shadow_worker', :b, 'ok')"
        ), {"b": (now - timedelta(seconds=30)).isoformat()})
        await s.commit()

    # Tick 1: warning (streak=1)
    first = await detect_per_symbol_prediction_freshness(factory)
    assert first[0].severity == "warning"
    assert first[0].details["streak"] == 1

    # Tick 2: critical (streak=2 crosses threshold)
    second = await detect_per_symbol_prediction_freshness(factory)
    assert second[0].severity == "critical", (
        f"second consecutive tick must escalate to critical, got "
        f"{second[0].severity}: {second[0].summary}"
    )
    assert "UNIVERSE STALL" in second[0].summary
    assert second[0].details["class"] == "universe_stall"
    assert second[0].details["streak"] == 2


@pytest.mark.asyncio
async def test_c3_universe_stall_streak_resets_on_clean_tick(
    bypass_c3_boot_grace,
) -> None:
    """A tick where the stall condition doesn't hold (e.g. fresh
    predictions arrived, or shadow_worker went stale) resets the streak.
    A subsequent stall observation must start from 1 again, not 2."""
    # Seed BOTH the stall symbols (SYM0..4/USDT) AND the recovery
    # symbol (OK/USDT) AND the tick-3 re-stall symbols (AGAIN0..4/USDT).
    engine = await _mk_predictions_engine(seed_universe=(
        [f"SYM{i}USDT" for i in range(5)]
        + ["OKUSDT"]
        + [f"AGAIN{i}USDT" for i in range(5)]
    ))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    # Tick 1: seed one universe-stall observation
    async with factory() as s:
        for i in range(5):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"SYM{i}/USDT",
                "t": (now - timedelta(hours=6)).isoformat(),
            })
        await s.execute(sa.text(
            "INSERT INTO worker_heartbeats "
            "(worker_name, beat_at, last_status) "
            "VALUES ('shadow_worker', :b, 'ok')"
        ), {"b": (now - timedelta(seconds=30)).isoformat()})
        await s.commit()
    first = await detect_per_symbol_prediction_freshness(factory)
    assert first[0].details["streak"] == 1

    # Tick 2: clear all stale rows, add a fresh row (simulate recovery)
    async with factory() as s:
        await s.execute(sa.text("DELETE FROM predictions"))
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('OK/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(minutes=30)).isoformat()})
        await s.commit()
    second = await detect_per_symbol_prediction_freshness(factory)
    assert second == [], "recovery tick should emit no findings"

    # Tick 3: re-seed the stall condition, assert streak restarts at 1
    async with factory() as s:
        await s.execute(sa.text("DELETE FROM predictions"))
        for i in range(5):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"AGAIN{i}/USDT",
                "t": (now - timedelta(hours=6)).isoformat(),
            })
        await s.commit()
    third = await detect_per_symbol_prediction_freshness(factory)
    assert third[0].severity == "warning"
    assert third[0].details["streak"] == 1, (
        f"streak should restart at 1 after recovery, got "
        f"{third[0].details['streak']}"
    )


@pytest.mark.asyncio
async def test_c3_universe_scope_drops_out_of_universe_stales(
    bypass_c3_boot_grace,
) -> None:
    """Universe-scope fix (2026-07-24): symbols with predictions that
    are NOT in the current asset_universe snapshot must be ignored.

    Regression against the 2026-07-24 staging observation: 89 stale
    findings per tick, dominated by symbols like EDEN/USDT (63 days
    stale) that hadn't been in the universe for months. Real per-symbol
    drops were invisible in the noise.
    """
    engine = await _mk_predictions_engine(
        # Current universe: ONLY these two symbols
        seed_universe=["BTCUSDT", "ETHUSDT"],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # BTC/USDT: fresh, in universe → NOT stale
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('BTC/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(minutes=30)).isoformat()})
        # ETH/USDT: fresh, in universe → NOT stale
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('ETH/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(minutes=30)).isoformat()})
        # EDEN/USDT: MASSIVELY stale but NOT in universe → must be ignored
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('EDEN/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(days=63)).isoformat()})
        # ALT/USDT: same — stale but out of universe
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('ALT/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(days=56)).isoformat()})
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert findings == [], (
        f"expected no findings — every in-universe symbol is fresh, "
        f"out-of-universe stales must be dropped. Got {findings}"
    )


@pytest.mark.asyncio
async def test_c3_universe_scope_counts_only_in_universe_stales(
    bypass_c3_boot_grace,
) -> None:
    """Precision check: stale_count reflects ONLY in-universe (symbol, tf)
    pairs, not the union of live + dead history."""
    engine = await _mk_predictions_engine(
        seed_universe=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # BTC/USDT: fresh (in universe)
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('BTC/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(minutes=30)).isoformat()})
        # ETH/USDT: stale (in universe → counts)
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('ETH/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(hours=3)).isoformat()})
        # SOL/USDT: stale (in universe → counts)
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('SOL/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(hours=5)).isoformat()})
        # Ancient dropouts: not in universe, must be ignored
        for old_sym in ("EDEN/USDT", "ALT/USDT", "BABY/USDT"):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:s, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {"s": old_sym, "t": (now - timedelta(days=30)).isoformat()})
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert len(findings) == 1
    # 2 stale (ETH, SOL) out of 3 in-universe → per_symbol_drop warning
    assert findings[0].severity == "warning"
    assert findings[0].details["class"] == "per_symbol_drop"
    assert findings[0].details["stale_count"] == 2, (
        f"stale_count must be 2 (ETH/SOL), got {findings[0].details['stale_count']}"
    )
    assert findings[0].details["total_pairs"] == 3, (
        f"total_pairs must be 3 (universe size), got "
        f"{findings[0].details['total_pairs']}"
    )
    # Sample must not include the ancient dropouts
    sample_syms = {item["symbol"] for item in findings[0].details["sample"]}
    assert sample_syms == {"ETH/USDT", "SOL/USDT"}


@pytest.mark.asyncio
async def test_c3_empty_universe_emits_anomaly_finding(
    bypass_c3_boot_grace,
) -> None:
    """Empty-universe guard: when asset_universe has zero USDT symbols
    (e.g. universe_refresh_task is down), C3 must NOT silently pass
    with zero findings. It emits a warning with class='universe_input_empty'.

    A detector that goes quiet because its input vanished is the worst
    failure mode — operator has no way to know detection stopped.
    """
    # NO seed_universe → asset_universe is empty
    engine = await _mk_predictions_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # Add some predictions — these should NOT be evaluated because
        # the universe query returns empty first.
        for i in range(5):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"SYM{i}/USDT",
                "t": (now - timedelta(hours=6)).isoformat(),
            })
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert len(findings) == 1, (
        f"empty universe must emit exactly one anomaly finding, got "
        f"{len(findings)}: {findings}"
    )
    assert findings[0].severity == "warning"
    assert findings[0].details["class"] == "universe_input_empty"
    assert "asset_universe" in findings[0].summary.lower() or (
        "empty" in findings[0].summary.lower()
    )


@pytest.mark.asyncio
async def test_c3_all_stale_without_fresh_shadow_worker_stays_warning(
    bypass_c3_boot_grace,
) -> None:
    """ALL symbols stale BUT shadow_worker heartbeat is ALSO stale/absent
    → warning, NOT critical. When the worker itself is down the watchdog
    already alarms; C3 should not double-alarm."""
    engine = await _mk_predictions_engine(
        seed_universe=[f"SYM{i}USDT" for i in range(5)],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(5):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"SYM{i}/USDT",
                "t": (now - timedelta(hours=6)).isoformat(),
            })
        # NO shadow_worker heartbeat inserted → shadow_fresh = False
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].details["class"] == "per_symbol_drop"
    assert findings[0].details["shadow_worker_fresh"] is False


# ---- C4 tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_c4_high_blocked_rate_alarms_info() -> None:
    """95% of telegram_signals blocked over the window → info.

    Sample size 20 is well above the C4_MIN_SAMPLE_SIZE guard.
    """
    engine = await _mk_telegram_signals_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # 19 blocked + 1 approved = 95%
        for i in range(19):
            await s.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, sent_at, response) VALUES (:i, :t, 'auto_skipped')"
            ), {"i": f"sig_blocked_{i}",
                "t": (now - timedelta(minutes=30)).isoformat()})
        await s.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, sent_at, response) VALUES ('sig_ok', :t, 'approved')"
        ), {"t": (now - timedelta(minutes=30)).isoformat()})
        await s.commit()

    findings = await detect_blocked_rate_anomaly(factory)
    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert "19/20" in findings[0].summary


@pytest.mark.asyncio
async def test_c4_min_sample_guard_suppresses_small_windows() -> None:
    """2/2 = 100% blocked is noise, not detection. The min-sample guard
    must suppress the finding until at least C4_MIN_SAMPLE_SIZE signals
    exist in the lookback window.

    Regression against the 2026-07-24 prod-promotion #27 first tick that
    fired a 2/2 = 100% finding immediately after container boot.
    """
    engine = await _mk_telegram_signals_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # 2 blocked out of 2 = 100%, but sample size < MIN_SAMPLE_SIZE
        for i in range(2):
            await s.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, sent_at, response) VALUES (:i, :t, 'auto_skipped')"
            ), {"i": f"sig_early_{i}",
                "t": (now - timedelta(minutes=30)).isoformat()})
        await s.commit()

    findings = await detect_blocked_rate_anomaly(factory)
    assert findings == [], (
        f"expected no finding below min-sample threshold, got {findings}"
    )


@pytest.mark.asyncio
async def test_c4_normal_blocked_rate_no_finding() -> None:
    engine = await _mk_telegram_signals_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        for i in range(3):
            await s.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, sent_at, response) VALUES (:i, :t, 'auto_skipped')"
            ), {"i": f"b{i}", "t": (now - timedelta(minutes=10)).isoformat()})
        for i in range(7):
            await s.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, sent_at, response) VALUES (:i, :t, 'approved')"
            ), {"i": f"a{i}", "t": (now - timedelta(minutes=10)).isoformat()})
        await s.commit()

    findings = await detect_blocked_rate_anomaly(factory)
    assert findings == []


@pytest.mark.asyncio
async def test_c4_zero_dispatches_no_finding() -> None:
    """No signals in window → C4 must NOT alarm on div-by-zero."""
    engine = await _mk_telegram_signals_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    findings = await detect_blocked_rate_anomaly(factory)
    assert findings == []


# ---- C3 fleet-eligible scope tests (2026-07-28) -------------------------


@pytest.mark.asyncio
async def test_c3_ranks_21_plus_are_out_of_scope(
    bypass_c3_boot_grace,
) -> None:
    """Symbols ranked beyond KEEPALIVE_TOP_N must NOT be flagged as
    stale even when their predictions are ancient. They are structurally
    outside the fleet's subscription set — ratified 2026-07-28 per
    docs/superpowers/decisions/2026-07-28-fleet-cap-top-20-ratified.md.

    Regression against the 9/27 permanent WARNING that fired every 5min
    tick between 2026-07-27 and 2026-07-28 for LINK/UNI/LTC/etc.
    (ranks 21-30).
    """
    # 21 symbols in universe (ranks 0..20). KEEPALIVE_TOP_N=20 means
    # the last one (rank 20) is OUT of the fleet slice ([:20] takes
    # ranks 0..19). Seed a stale prediction row for it — must NOT
    # produce a finding.
    universe = [f"IN{i}USDT" for i in range(20)] + ["OUTUSDT"]
    engine = await _mk_predictions_engine(seed_universe=universe)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # All 20 IN-fleet symbols fresh (30min old on 1h TF).
        for i in range(20):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"IN{i}/USDT",
                "t": (now - timedelta(minutes=30)).isoformat(),
            })
        # OUT-of-fleet symbol has an ancient prediction (50 days old).
        # This mirrors LINK's real state in prod on 2026-07-28.
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('OUT/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(days=50)).isoformat()})
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    # OUT/USDT (rank 20) must be filtered out. No stale finding.
    assert findings == [], (
        "rank >= KEEPALIVE_TOP_N must be structurally out of scope; "
        f"got findings: {findings}"
    )


@pytest.mark.asyncio
async def test_c3_btc_singleton_stays_expected_when_dropped_from_top_20(
    bypass_c3_boot_grace,
) -> None:
    """BTC/USDT/1h is handled by the singleton `live_worker` regardless
    of whether BTCUSDT is in the fleet's top-20 rank slice on any given
    day. DEFAULT_EXCLUDE (the fleet's "handled elsewhere" set) is
    unioned back into the expected set so C3 keeps monitoring BTC even
    if it drops out of the top-20 rank (rare but possible).

    Regression against a mis-implementation where the expected set is
    computed as `fleet_pairs - DEFAULT_EXCLUDE` and BTC goes dark for
    C3 on days it ranks below 20.
    """
    # 21-symbol universe where BTC is deliberately NOT in top-20 (rank
    # 20 — one past the cap). Every other slot is filler.
    universe = [f"FILL{i}USDT" for i in range(20)] + ["BTCUSDT"]
    engine = await _mk_predictions_engine(seed_universe=universe)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        # All 20 filler symbols fresh.
        for i in range(20):
            await s.execute(sa.text(
                "INSERT INTO predictions "
                "(symbol, timeframe, ts, direction, final_score, confidence) "
                "VALUES (:sym, '1h', :t, 'LONG', 0.3, 0.6)"
            ), {
                "sym": f"FILL{i}/USDT",
                "t": (now - timedelta(minutes=30)).isoformat(),
            })
        # BTC is stale (3 hours on 1h TF). Must be flagged even though
        # BTCUSDT is at rank 20 (out of top-20 fleet slice) — the
        # singleton is still expected to write it.
        await s.execute(sa.text(
            "INSERT INTO predictions "
            "(symbol, timeframe, ts, direction, final_score, confidence) "
            "VALUES ('BTC/USDT', '1h', :t, 'LONG', 0.3, 0.6)"
        ), {"t": (now - timedelta(hours=3)).isoformat()})
        await s.commit()

    findings = await detect_per_symbol_prediction_freshness(factory)
    assert len(findings) == 1, findings
    assert findings[0].severity == "warning"
    assert findings[0].details["stale_count"] == 1
    sample = findings[0].details["sample"]
    assert any(item["symbol"] == "BTC/USDT" for item in sample), (
        "BTC/USDT must remain expected via DEFAULT_EXCLUDE union even "
        "when it drops out of the top-20 fleet slice; got sample: "
        f"{sample}"
    )
