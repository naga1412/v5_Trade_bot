"""PR-FIX-PR275-FOLLOWUP (2026-05-27): Postgres-only verification that
alembic 0029 extends `live_trades_approved_via_check` to include the
two new values `'telegram'` and `'hybrid'`.

WHY POSTGRES-ONLY
=================
SQLite test fixtures don't enforce the CHECK constraint on dynamically-
inserted values (the migration adds the constraint via raw SQL that
SQLite ignores). Verifying the actual constraint requires the real
Postgres test DB the CI backend job runs against.

WHAT THIS TESTS
===============
1. After 0029 runs, `approved_via='telegram'` INSERTs SUCCEED.
   (This was the prod bug — every `_place_approved_order` Phase 1
   INSERT failed CheckViolation, hiding the orphan symptom.)
2. After 0029 runs, `approved_via='hybrid'` INSERTs SUCCEED.
   (Reserved for a future PR that may distinguish placed_hybrid from
   straight auto.)
3. The pre-existing allowed values (`telegram-button`, `auto`,
   `manual-button`) STILL succeed — additive change, no regression.
4. An unrecognised value (e.g. `'discord'`) STILL fails CheckViolation.

POSTGRES-ONLY
=============
Skipped on SQLite. CI's backend job runs `alembic upgrade head`
against a Postgres test DB before pytest, so 0029 is in place when
these tests run.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


_DSN = os.environ.get("DATABASE_URL", "")
_IS_PG = _DSN.startswith("postgresql") or _DSN.startswith("postgres")

pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="Postgres-only — set DATABASE_URL=postgresql+asyncpg://... to run.",
)


# Sentinel keys so test rows don't collide; cleaned up before + after.
_TEST_USER_ID: int = 1  # bootstrap admin (FK constraint)
_TEST_SYMBOL_PREFIX: str = "__pr_fix_pr275_check_test__"


@pytest.fixture
async def _engine():
    engine = create_async_engine(_DSN)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "DELETE FROM live_trades WHERE symbol LIKE :pat"
        ), {"pat": _TEST_SYMBOL_PREFIX + "%"})
    yield engine
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "DELETE FROM live_trades WHERE symbol LIKE :pat"
        ), {"pat": _TEST_SYMBOL_PREFIX + "%"})
    await engine.dispose()


async def _try_insert(
    engine, *, approved_via: str, sym_tag: str,
) -> tuple[bool, str | None]:
    """Attempt an INSERT with the given approved_via. Returns
    (succeeded, error_message)."""
    # `prev_hash` and `row_hash` are NOT NULL in prod (audit-chain
    # columns). The CHECK constraint we're testing runs AFTER NOT NULL
    # checks, so we need placeholder values to actually reach the
    # CHECK — otherwise every INSERT fails NotNullViolation first and
    # the test conflates two failure classes. The values are
    # 64-character hex (matches sha256 output shape); the real chain
    # is irrelevant here — we DELETE before/after.
    # Per-row unique placeholders. Both `binance_order_id` AND
    # `row_hash` carry UNIQUE constraints in prod (migration 0016 +
    # audit-chain). Reusing the same placeholder across the 3-row
    # `test_existing_allowed_values_still_pass` test triggered
    # UniqueViolations that masked the CHECK we're trying to verify.
    # Hash placeholders are 64-char hex (matches sha256 shape) seeded
    # from sym_tag so they're deterministic per-row + unique.
    import hashlib as _h
    seed = (sym_tag + ":hash").encode()
    _ROW_HASH = _h.sha256(seed).hexdigest()
    _PREV_HASH = _h.sha256(seed + b":prev").hexdigest()
    unique_boid = f"_test_boid_{sym_tag.lstrip('_')}"
    async with AsyncSession(engine) as s:
        try:
            await s.execute(sa.text(
                "INSERT INTO live_trades "
                "(user_id, symbol, direction, margin_usdt, leverage, "
                " position_value_usdt, entry_price, stop_loss, take_profit, "
                " binance_order_id, opened_at, mode_at_open, approved_via, "
                " reasoning, inputs_hash, status, prev_hash, row_hash) "
                "VALUES (:u, :sym, 'LONG', 10.0, 5, 50.0, "
                "        80000.0, 79600.0, 80400.0, :boid, NOW(), "
                "        'manual', :av, '{}', 'abc', 'closed', "
                "        :ph, :rh)"
            ), {
                "u": _TEST_USER_ID,
                "sym": _TEST_SYMBOL_PREFIX + sym_tag,
                "av": approved_via,
                "boid": unique_boid,
                "ph": _PREV_HASH,
                "rh": _ROW_HASH,
            })
            await s.commit()
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]


@pytest.mark.asyncio
async def test_approved_via_telegram_passes_check(_engine) -> None:
    """The exact prod bug: `approved_via='telegram'` from
    `_place_approved_order` must NOT trigger CheckViolation post-0029."""
    ok, err = await _try_insert(
        _engine, approved_via="telegram", sym_tag="_telegram",
    )
    assert ok, f"approved_via='telegram' rejected by CHECK: {err}"


@pytest.mark.asyncio
async def test_approved_via_hybrid_passes_check(_engine) -> None:
    """Defensive addition — 'hybrid' reserved for a future PR that
    differentiates placed_hybrid from straight auto."""
    ok, err = await _try_insert(
        _engine, approved_via="hybrid", sym_tag="_hybrid",
    )
    assert ok, f"approved_via='hybrid' rejected by CHECK: {err}"


@pytest.mark.asyncio
async def test_existing_allowed_values_still_pass(_engine) -> None:
    """Additive migration — pre-existing values must continue to pass."""
    for av, tag in (
        ("telegram-button", "_tg_btn"),
        ("auto", "_auto"),
        ("manual-button", "_man_btn"),
    ):
        ok, err = await _try_insert(_engine, approved_via=av, sym_tag=tag)
        assert ok, f"approved_via='{av}' rejected by CHECK: {err}"


@pytest.mark.asyncio
async def test_unrecognised_value_still_fails(_engine) -> None:
    """Negative control — the CHECK is still in effect; unknown values
    must still fail CheckViolation. Catches a regression where the
    migration accidentally widens the constraint too much (e.g. drops
    the CHECK entirely)."""
    ok, err = await _try_insert(
        _engine, approved_via="discord", sym_tag="_discord",
    )
    assert not ok, "approved_via='discord' should have been rejected"
    assert err is not None
    assert (
        "check" in err.lower()
        or "violates" in err.lower()
        or "approved_via" in err.lower()
    ), f"Unexpected error type (not a CheckViolation): {err}"
