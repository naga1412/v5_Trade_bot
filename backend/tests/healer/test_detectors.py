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
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.healer import detectors
from app.healer.detectors import (
    detect_blocked_rate_anomaly,
    detect_dispatch_error_rate,
    detect_per_symbol_prediction_freshness,
    detect_score_distribution_anomaly,
)
from app.healer.findings import DISPATCH_EXCEPTION_DETECTOR


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


async def _mk_predictions_engine():
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
async def test_c3_stale_symbol_alarms_warning() -> None:
    """One symbol's prediction is >2× TF old → warning."""
    engine = await _mk_predictions_engine()
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
    sample = findings[0].details["sample"]
    assert any(item["symbol"] == "STALE/USDT" for item in sample)


@pytest.mark.asyncio
async def test_c3_all_symbols_fresh_no_finding() -> None:
    engine = await _mk_predictions_engine()
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


# ---- C4 tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_c4_high_blocked_rate_alarms_info() -> None:
    """95% of telegram_signals blocked over the window → info."""
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
