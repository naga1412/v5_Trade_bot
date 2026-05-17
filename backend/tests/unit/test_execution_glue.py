"""SP-8 Phase J — execution glue (vault cache + context builder)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.secrets.vault import encrypt_secrets
from app.trading.execution.glue import (
    build_user_context,
    initialize_vault_cache,
    proposal_from_prediction,
    reset_vault_cache_for_tests,
    vault_keys,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_vault_cache_for_tests()
    yield
    reset_vault_cache_for_tests()


# ---- Vault cache --------------------------------------------------------


def test_initialize_returns_false_when_file_missing(tmp_path: Path) -> None:
    ok = initialize_vault_cache(
        passphrase="x" * 16, secrets_path=tmp_path / "ghost.enc",
    )
    assert ok is False
    assert vault_keys() is None


def test_initialize_returns_false_on_wrong_passphrase(tmp_path: Path) -> None:
    blob = encrypt_secrets(
        {"binance_api_key": "k", "binance_api_secret": "s"},
        passphrase="correct-passphrase-2026",
    )
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    ok = initialize_vault_cache(
        passphrase="wrong-passphrase-2026-x", secrets_path=p,
    )
    assert ok is False
    assert vault_keys() is None


def test_initialize_returns_false_when_keys_missing(tmp_path: Path) -> None:
    blob = encrypt_secrets(
        {"telegram_bot_token": "t"},
        passphrase="correct-passphrase-2026",
    )
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    ok = initialize_vault_cache(
        passphrase="correct-passphrase-2026", secrets_path=p,
    )
    assert ok is False


def test_initialize_caches_keys_on_success(tmp_path: Path) -> None:
    blob = encrypt_secrets(
        {"binance_api_key": "kk", "binance_api_secret": "ss"},
        passphrase="correct-passphrase-2026",
    )
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    ok = initialize_vault_cache(
        passphrase="correct-passphrase-2026", secrets_path=p,
    )
    assert ok is True
    keys = vault_keys()
    assert keys is not None
    assert keys.binance_api_key == "kk"
    assert keys.binance_api_secret == "ss"


# ---- proposal_from_prediction ------------------------------------------


def test_proposal_neutral_returns_none() -> None:
    p = proposal_from_prediction(
        symbol="BTC/USDT", timeframe="1h",
        pred_direction="NEUTRAL",
        pred_confidence=0.5, layer_summary={}, inputs_hash="x",
        entry_price=80_000, stop_loss_price=78_400,
        take_profit_price=83_000,
    )
    assert p is None


def test_proposal_long_builds_signal() -> None:
    p = proposal_from_prediction(
        symbol="BTC/USDT", timeframe="1h",
        pred_direction="LONG",
        pred_confidence=0.72, layer_summary={"L1": {"score": 0.85}},
        inputs_hash="abc",
        entry_price=80_000, stop_loss_price=78_400,
        take_profit_price=83_000,
    )
    assert p is not None
    assert p.direction == "LONG"
    assert p.confidence_pct == 72


def test_proposal_invalid_prices_returns_none() -> None:
    p = proposal_from_prediction(
        symbol="BTC/USDT", timeframe="1h",
        pred_direction="LONG", pred_confidence=0.5, layer_summary={},
        inputs_hash="x",
        entry_price=0, stop_loss_price=78_400, take_profit_price=83_000,
    )
    assert p is None


# ---- PR2: MTF threading -------------------------------------------------


def _proposal_baseline_kwargs() -> dict:
    """Minimal kwargs for a LONG proposal — shared by the MTF threading tests."""
    return dict(
        symbol="BTC/USDT", timeframe="1h",
        pred_direction="LONG", pred_confidence=0.72,
        layer_summary={"L1": {"score": 0.85}},
        inputs_hash="abc",
        entry_price=80_000, stop_loss_price=78_400, take_profit_price=83_000,
    )


def test_proposal_from_prediction_threads_mtf_fields() -> None:
    """When the caller passes mtf_*, proposal_from_prediction threads
    them through to SignalProposal. mtf_directions_json is parsed to a dict."""
    p = proposal_from_prediction(
        **_proposal_baseline_kwargs(),
        mtf_agreement=4,
        mtf_dominant_tf="1h",
        mtf_directions_json='{"5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0}',
    )
    assert p is not None
    assert p.mtf_agreement == 4
    assert p.mtf_dominant_tf == "1h"
    assert p.mtf_directions == {
        "5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0,
    }


def test_proposal_from_prediction_no_mtf_threads_none() -> None:
    """When the caller omits mtf_* (PR1 / legacy path), proposal MTF
    fields are None — bit-identical to pre-PR2."""
    p = proposal_from_prediction(**_proposal_baseline_kwargs())
    assert p is not None
    assert p.mtf_agreement is None
    assert p.mtf_dominant_tf is None
    assert p.mtf_directions is None


def test_proposal_from_prediction_invalid_mtf_json_fails_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed mtf_directions_json must NOT poison dispatch. The parse
    helper returns None + logs a warning; the other mtf_* fields still
    thread through. Fail-open per spec §4.3."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.trading.execution.glue")
    p = proposal_from_prediction(
        **_proposal_baseline_kwargs(),
        mtf_agreement=4,
        mtf_dominant_tf="1h",
        mtf_directions_json="not-valid-json",
    )
    assert p is not None
    assert p.mtf_agreement == 4
    assert p.mtf_directions is None
    assert any("mtf_directions_json" in r.message for r in caplog.records)


def test_proposal_from_prediction_mtf_directions_non_dict_fails_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """JSON parses to a list, not a dict → treat as malformed (parse helper
    returns None + warns)."""
    import logging
    caplog.set_level(logging.WARNING, logger="app.trading.execution.glue")
    p = proposal_from_prediction(
        **_proposal_baseline_kwargs(),
        mtf_directions_json='[1, 2, 3]',
    )
    assert p is not None
    assert p.mtf_directions is None
    assert any("mtf_directions_json" in r.message for r in caplog.records)


# ---- build_user_context -------------------------------------------------


async def _setup_users(*, mode: str = "manual") -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, "
            "trading_mode TEXT NOT NULL DEFAULT 'manual', "
            "position_sizing_mode TEXT NOT NULL DEFAULT 'fixed', "
            "fixed_size_min_usdt REAL DEFAULT 30, "
            "fixed_size_max_usdt REAL DEFAULT 30, "
            "max_leverage_cap INTEGER DEFAULT 10, "
            "max_concurrent_positions INTEGER DEFAULT 5)"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, trading_mode) VALUES (1, :m)"
        ), {"m": mode})
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, margin_usdt REAL, pnl_usdt REAL, "
            "closed_at TEXT)"
        ))
    return engine


@pytest.mark.asyncio
async def test_build_context_returns_none_when_vault_uninitialised() -> None:
    engine = await _setup_users()
    async with AsyncSession(engine) as s:
        ctx = await build_user_context(s, user_id=1, use_testnet=True)
    assert ctx is None  # vault never initialised


@pytest.mark.asyncio
async def test_build_context_happy_path(tmp_path: Path) -> None:
    blob = encrypt_secrets(
        {"binance_api_key": "kk", "binance_api_secret": "ss"},
        passphrase="correct-passphrase-2026",
    )
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    initialize_vault_cache(
        passphrase="correct-passphrase-2026", secrets_path=p,
    )

    engine = await _setup_users(mode="telegram-approve")
    async with AsyncSession(engine) as s:
        ctx = await build_user_context(s, user_id=1, use_testnet=True)
    assert ctx is not None
    assert ctx.user_id == 1
    assert ctx.mode == "telegram-approve"
    assert ctx.binance_api_key == "kk"
    assert ctx.use_testnet is True
    assert ctx.fixed_size_usdt == 30.0
    assert ctx.max_leverage_cap == 10
    assert ctx.open_positions_count == 0


@pytest.mark.asyncio
async def test_build_context_counts_open_positions(tmp_path: Path) -> None:
    blob = encrypt_secrets(
        {"binance_api_key": "kk", "binance_api_secret": "ss"},
        passphrase="correct-passphrase-2026",
    )
    p = tmp_path / "secrets.enc"
    p.write_bytes(blob)
    initialize_vault_cache(
        passphrase="correct-passphrase-2026", secrets_path=p,
    )
    engine = await _setup_users()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO live_trades (user_id, margin_usdt, pnl_usdt, closed_at) "
            "VALUES (1, 30, NULL, NULL), (1, 30, NULL, NULL)"
        ))
        await s.commit()
        ctx = await build_user_context(s, user_id=1, use_testnet=True)
    assert ctx is not None
    assert ctx.open_positions_count == 2
