"""End-to-end Phase D4: tamper a predictions row_hash → verifier detects.

Builds a self-contained SQLite engine with the SP-0 chained `predictions`
schema and the SP-0.7 `auth_violations` schema, writes 3 valid rows via
``insert_with_chain``, mutates the middle row's ``row_hash`` to garbage,
then drives ONE verifier round via ``_check_all_chains``. Asserts:

    1. ``alert_admin`` was called (mocked) with severity='critical' and a
       message naming the tampered table
    2. An ``auth_violations`` row landed with attempted_email='system' and
       reason matching ``audit_chain_broken:predictions:<row_id>``

The test never invokes ``run_audit_verifier_loop`` (that loops forever) —
it calls the inner one-shot helper directly per the SP-7 plan.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.audit import insert_with_chain
from app.ops import verifier_scheduler as vs


@pytest_asyncio.fixture
async def chained_predictions_factory() -> Any:
    """Engine with chained `predictions` + `auth_violations` tables.

    SQLite-friendly mirror of the production schemas:
      - migration 0002_audit_chain (predictions: hash-chain columns)
      - migration 0004 (auth_violations: attempted_email, reason)
    Plus the SP-0.7 `user_id` column the live persist_prediction populates.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "ts TEXT NOT NULL, "
            "layer_scores TEXT NOT NULL, "
            "final_score REAL NOT NULL, "
            "direction TEXT, "
            "confidence REAL, "
            "inputs_hash TEXT NOT NULL, "
            "model_version TEXT NOT NULL DEFAULT 'sp-0', "
            "cold_start INTEGER NOT NULL DEFAULT 1, "
            "prev_hash TEXT NOT NULL, "
            "row_hash TEXT NOT NULL UNIQUE)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE auth_violations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "attempted_email TEXT NOT NULL, "
            "attempted_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "reason TEXT NOT NULL, "
            "jwt_sub TEXT, "
            "request_path TEXT)"
        ))
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verifier_detects_tampered_predictions_row(
    chained_predictions_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Insert valid rows, tamper one, run verifier, assert detection."""
    factory = chained_predictions_factory

    # 1. Seed three valid hash-chained predictions rows.
    async with factory() as session:
        for i in range(3):
            await insert_with_chain(session, "predictions", {
                "user_id": 1,
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "ts": datetime(2025, 1, 1, i, tzinfo=timezone.utc).isoformat(),
                "layer_scores": json.dumps({"layer_a": 0.5}),
                "final_score": 65.0 + i,
                "direction": "LONG",
                "confidence": 0.7,
                "inputs_hash": f"h{i}",
                "model_version": "sp-0",
                "cold_start": 1,
            })
        await session.commit()

        # 2. Tamper: corrupt row 2's row_hash directly. verify_chain will
        # see the actual row_hash mismatch the recomputed hash → ok=False.
        await session.execute(sa.text(
            "UPDATE predictions SET row_hash = "
            "'0000000000000000000000000000000000000000000000000000000000000bad' "
            "WHERE id = 2"
        ))
        await session.commit()

    # 3. Mock alert_admin and narrow CHAINED_TABLES to predictions only —
    # paper_trades + shadow_trades aren't in this test's schema.
    alerts: list[tuple[str, str]] = []

    async def _fake_alert(msg: str, *, severity: str = "warning") -> bool:
        alerts.append((msg, severity))
        return True

    monkeypatch.setattr(vs, "alert_admin", _fake_alert)
    monkeypatch.setattr(vs, "CHAINED_TABLES", {
        "predictions": vs.CHAINED_TABLES["predictions"],
    })

    # 4. One-shot verifier round (no time loop, no sleep).
    await vs._check_all_chains(factory)

    # 5. Assert: alert fired exactly once, severity critical, msg names table.
    assert len(alerts) == 1, f"expected 1 alert, got {alerts}"
    msg, severity = alerts[0]
    assert severity == "critical"
    assert "predictions" in msg
    assert "audit chain broken" in msg.lower()

    # 6. Assert: auth_violations row was written with the parseable reason.
    async with factory() as session:
        rows = (await session.execute(sa.text(
            "SELECT attempted_email, reason FROM auth_violations "
            "WHERE attempted_email = 'system'"
        ))).all()
    assert len(rows) >= 1
    assert any(
        r.reason.startswith("audit_chain_broken:predictions:")
        for r in rows
    )


@pytest.mark.asyncio
async def test_verifier_clean_chain_does_not_alert(
    chained_predictions_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untampered chain → no alert, no auth_violations row."""
    factory = chained_predictions_factory

    async with factory() as session:
        for i in range(3):
            await insert_with_chain(session, "predictions", {
                "user_id": 1,
                "symbol": "ETH/USDT",
                "timeframe": "1h",
                "ts": datetime(2025, 2, 1, i, tzinfo=timezone.utc).isoformat(),
                "layer_scores": json.dumps({"layer_a": 0.4}),
                "final_score": 50.0 + i,
                "direction": "SHORT",
                "confidence": 0.6,
                "inputs_hash": f"clean-{i}",
                "model_version": "sp-0",
                "cold_start": 0,
            })
        await session.commit()

    alerts: list[tuple[str, str]] = []

    async def _fake_alert(msg: str, *, severity: str = "warning") -> bool:
        alerts.append((msg, severity))
        return True

    monkeypatch.setattr(vs, "alert_admin", _fake_alert)
    monkeypatch.setattr(vs, "CHAINED_TABLES", {
        "predictions": vs.CHAINED_TABLES["predictions"],
    })

    await vs._check_all_chains(factory)

    assert alerts == []
    async with factory() as session:
        violations = (await session.execute(sa.text(
            "SELECT 1 FROM auth_violations WHERE attempted_email = 'system'"
        ))).all()
    assert violations == []
