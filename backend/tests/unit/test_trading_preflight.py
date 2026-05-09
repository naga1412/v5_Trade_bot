"""SP-8 Phase J — pre-flight tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.secrets.vault import encrypt_secrets
from app.trading.preflight import (
    check_audit_chain_intact,
    check_binance_permissions,
    check_master_passphrase,
    check_migration_0016_applied,
    check_vault_decrypt,
    run_preflight,
)


# ---- check_master_passphrase --------------------------------------------


def test_passphrase_check_fails_when_unset() -> None:
    with patch.dict("os.environ", {"MASTER_PASSPHRASE": ""}, clear=False):
        r = check_master_passphrase()
    assert r.passed is False
    assert "empty" in r.detail


def test_passphrase_check_fails_when_too_short() -> None:
    with patch.dict("os.environ", {"MASTER_PASSPHRASE": "short-pp"}, clear=False):
        r = check_master_passphrase()
    assert r.passed is False
    assert "16" in r.detail


def test_passphrase_check_passes_when_strong() -> None:
    with patch.dict(
        "os.environ", {"MASTER_PASSPHRASE": "a-strong-passphrase-2026"}, clear=False,
    ):
        r = check_master_passphrase()
    assert r.passed is True


# ---- check_vault_decrypt ------------------------------------------------


def test_vault_check_fails_when_file_missing(tmp_path: Path) -> None:
    r = check_vault_decrypt(
        secrets_path=tmp_path / "nonexistent.enc",
        passphrase="strong-passphrase-2026-x",
    )
    assert r.passed is False
    assert "does not exist" in r.detail


def test_vault_check_fails_when_passphrase_missing(tmp_path: Path) -> None:
    blob = encrypt_secrets({"x": "y"}, passphrase="correct-passphrase-2026")
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    r = check_vault_decrypt(secrets_path=p, passphrase="")
    assert r.passed is False
    assert "MASTER_PASSPHRASE not set" in r.detail


def test_vault_check_fails_when_passphrase_wrong(tmp_path: Path) -> None:
    blob = encrypt_secrets({"x": "y"}, passphrase="correct-passphrase-2026")
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    r = check_vault_decrypt(secrets_path=p, passphrase="wrong-passphrase-2026")
    assert r.passed is False
    assert "decrypt failed" in r.detail


def test_vault_check_passes_with_correct_passphrase(tmp_path: Path) -> None:
    blob = encrypt_secrets(
        {"binance_api_key": "k", "binance_api_secret": "s"},
        passphrase="correct-passphrase-2026",
    )
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    r = check_vault_decrypt(
        secrets_path=p, passphrase="correct-passphrase-2026",
    )
    assert r.passed is True
    assert "decrypted 2" in r.detail


# ---- check_binance_permissions ------------------------------------------


@pytest.mark.asyncio
async def test_binance_check_passes_when_perms_safe() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "enableReading": True, "enableFutures": True,
            "enableWithdrawals": False,
            "enableInternalTransfer": False,
            "permitsUniversalTransfer": False,
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        r = await check_binance_permissions(
            api_key="k", api_secret="s", http=http,
        )
    assert r.passed is True


@pytest.mark.asyncio
async def test_binance_check_fails_when_withdrawals_enabled() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "enableWithdrawals": True,
            "enableInternalTransfer": False,
            "permitsUniversalTransfer": False,
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        r = await check_binance_permissions(
            api_key="k", api_secret="s", http=http,
        )
    assert r.passed is False
    assert "enableWithdrawals" in r.detail


@pytest.mark.asyncio
async def test_binance_check_fails_when_api_returns_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"msg": "API key invalid"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        r = await check_binance_permissions(
            api_key="k", api_secret="s", http=http,
        )
    assert r.passed is False


# ---- check_migration_0016_applied ---------------------------------------


async def _setup_pg_mocks(
    *, has_migration: bool, has_tables: bool = True,
    include_chain_tables: bool = False,
) -> Any:
    """In-memory SQLite that simulates the alembic_version + SP-8 tables.

    When include_chain_tables=True, also creates the audit-chained tables
    that run_preflight's check_audit_chain_intact walks (so the
    integration tests don't fail with 'no such table: predictions').
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE alembic_version (version_num TEXT)"
        ))
        if has_migration:
            await conn.execute(sa.text(
                "INSERT INTO alembic_version VALUES ('0016_sp8_trading')"
            ))
        if has_tables:
            await conn.execute(sa.text(
                "CREATE TABLE live_trades (id INT, prev_hash TEXT, row_hash TEXT)"
            ))
            await conn.execute(sa.text(
                "CREATE TABLE tax_events (id INT, prev_hash TEXT, row_hash TEXT)"
            ))
        if include_chain_tables:
            for tbl in (
                "predictions", "paper_trades", "shadow_trades",
                "brain_decisions", "mode_change_log",
            ):
                await conn.execute(sa.text(
                    f"CREATE TABLE {tbl} (id INT PRIMARY KEY, "
                    "prev_hash TEXT, row_hash TEXT)"
                ))
    return engine


@pytest.mark.asyncio
async def test_migration_check_passes_when_tables_exist() -> None:
    engine = await _setup_pg_mocks(has_migration=True, has_tables=True)
    async with AsyncSession(engine) as s:
        r = await check_migration_0016_applied(s)
    assert r.passed is True


@pytest.mark.asyncio
async def test_migration_check_fails_when_alembic_empty() -> None:
    engine = await _setup_pg_mocks(has_migration=False, has_tables=True)
    async with AsyncSession(engine) as s:
        r = await check_migration_0016_applied(s)
    assert r.passed is False
    assert "no alembic_version" in r.detail


@pytest.mark.asyncio
async def test_migration_check_fails_when_tables_missing() -> None:
    engine = await _setup_pg_mocks(has_migration=True, has_tables=False)
    async with AsyncSession(engine) as s:
        r = await check_migration_0016_applied(s)
    assert r.passed is False
    assert "missing" in r.detail.lower()


# ---- check_audit_chain_intact -------------------------------------------


async def _setup_chain_db(*, intact: bool = True) -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INT PRIMARY KEY, "
            "prev_hash TEXT, row_hash TEXT)"
        ))
        await conn.execute(sa.text(
            "INSERT INTO predictions VALUES (1, 'genesis', 'hashA')"
        ))
        # If intact, row 2's prev_hash = 'hashA'; otherwise mismatched.
        prev_for_2 = "hashA" if intact else "tampered"
        await conn.execute(sa.text(
            f"INSERT INTO predictions VALUES (2, '{prev_for_2}', 'hashB')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO predictions VALUES (3, 'hashB', 'hashC')"
        ))
    return engine


@pytest.mark.asyncio
async def test_audit_chain_intact_when_links_match() -> None:
    engine = await _setup_chain_db(intact=True)
    async with AsyncSession(engine) as s:
        r = await check_audit_chain_intact(s, tables=["predictions"])
    assert r.passed is True


@pytest.mark.asyncio
async def test_audit_chain_break_detected() -> None:
    engine = await _setup_chain_db(intact=False)
    async with AsyncSession(engine) as s:
        r = await check_audit_chain_intact(s, tables=["predictions"])
    assert r.passed is False
    assert "chain break in predictions" in r.detail


@pytest.mark.asyncio
async def test_audit_chain_empty_table_passes() -> None:
    """Empty table is fine — chain starts at the first insert."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INT, prev_hash TEXT, row_hash TEXT)"
        ))
    async with AsyncSession(engine) as s:
        r = await check_audit_chain_intact(s, tables=["predictions"])
    assert r.passed is True


# ---- run_preflight orchestrator ----------------------------------------


@pytest.mark.asyncio
async def test_run_preflight_aggregates_all_checks(tmp_path: Path) -> None:
    blob = encrypt_secrets(
        {"binance_api_key": "k", "binance_api_secret": "s"},
        passphrase="correct-passphrase-2026",
    )
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "enableReading": True, "enableFutures": True,
            "enableWithdrawals": False,
            "enableInternalTransfer": False,
            "permitsUniversalTransfer": False,
        })

    engine = await _setup_pg_mocks(
        has_migration=True, has_tables=True, include_chain_tables=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncSession(engine) as s:
            with patch.dict(
                "os.environ", {"MASTER_PASSPHRASE": "correct-passphrase-2026"},
            ):
                result = await run_preflight(
                    s, secrets_path=p, http=http,
                )

    assert result.all_passed is True
    assert len(result.checks) == 5
    assert result.summary_line() == "preflight: 5/5 checks passed"


@pytest.mark.asyncio
async def test_run_preflight_short_circuits_when_passphrase_missing(
    tmp_path: Path,
) -> None:
    """No passphrase → vault check skipped + Binance skipped (depends on
    keys), but DB checks still run."""
    engine = await _setup_pg_mocks(
        has_migration=True, has_tables=True, include_chain_tables=True,
    )
    async with AsyncSession(engine) as s:
        with patch.dict("os.environ", {"MASTER_PASSPHRASE": ""}):
            result = await run_preflight(
                s, secrets_path=tmp_path / "secrets.enc",
            )

    assert result.all_passed is False
    failures = result.failures()
    assert any(f.name == "master_passphrase_set" for f in failures)
    assert any(f.name == "vault_decrypt_ok" for f in failures)
    # DB checks still ran and passed.
    db_results = [c for c in result.checks if c.name in (
        "migration_0016_applied", "audit_chain_intact",
    )]
    assert all(c.passed for c in db_results)
