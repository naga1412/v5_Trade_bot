"""SP-8 Phase A: migration 0016 trading-mode infrastructure.

Source-scan tests that match the conventions in test_migration_0015_rl_brain.py:
load by file path, verify the chain link + every table declaration + every
hash-chain column + dialect branches.

Spec: docs/superpowers/specs/2026-05-03-autonomous-trading-design.md §14.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2026_05_09_0016_sp8_trading_tables.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_sp8_migration_0016", _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_source() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_chain_links_to_0015_rl_brain() -> None:
    module = _load_migration()
    assert module.revision == "0016_sp8_trading"
    assert module.down_revision == "0015_rl_brain"


def test_migration_creates_all_seven_tables_in_postgres() -> None:
    """Postgres branch creates all 7 SP-8 tables."""
    src = _migration_source()
    for table in (
        "mode_change_log",
        "live_trades",
        "telegram_signals",
        "hardware_confirms",
        "kill_switch_state",
        "tax_events",
        "fx_rates",
    ):
        assert f"CREATE TABLE {table}" in src, (
            f"missing CREATE TABLE for {table}"
        )


def test_migration_drops_all_tables_on_downgrade() -> None:
    src = _migration_source()
    for table in (
        "fx_rates", "tax_events", "kill_switch_state",
        "hardware_confirms", "telegram_signals", "live_trades",
        "mode_change_log",
    ):
        assert f"DROP TABLE IF EXISTS {table}" in src


def test_users_table_extended_with_three_columns() -> None:
    """Spec §14 — users gets trading_mode + totp_secret_encrypted + telegram_chat_id."""
    src = _migration_source()
    for col in ("trading_mode", "totp_secret_encrypted", "telegram_chat_id"):
        assert f"ADD COLUMN {col}" in src


def test_trading_mode_check_enumerates_all_three_modes() -> None:
    src = _migration_source()
    for mode in ("manual", "telegram-approve", "fully-auto"):
        assert f"'{mode}'" in src, f"trading_mode CHECK missing `{mode}`"


def test_hash_chained_tables_carry_audit_columns() -> None:
    """live_trades, tax_events, mode_change_log are hash-chained — SP-7
    verify_chain picks them up automatically when they have these columns."""
    src = _migration_source()
    for table in ("live_trades", "tax_events", "mode_change_log"):
        # Find the table block; ensure both prev_hash and row_hash appear in it.
        block_start = src.find(f"CREATE TABLE {table}")
        assert block_start != -1
        block_end = src.find(");", block_start)
        block = src[block_start:block_end]
        assert "prev_hash" in block, f"{table} missing prev_hash"
        assert "row_hash" in block, f"{table} missing row_hash"


def test_live_trades_required_columns_per_spec_section_14() -> None:
    src = _migration_source()
    for col in (
        "binance_order_id", "leverage", "margin_usdt",
        "position_value_usdt", "stop_loss", "take_profit",
        "liquidation_price", "mode_at_open", "approved_via",
        "fees_paid_usdt", "funding_paid_usdt",
    ):
        assert col in src, f"live_trades must declare `{col}`"


def test_tax_events_required_columns_per_spec_section_8_1() -> None:
    src = _migration_source()
    for col in (
        "trade_id", "tds_owed_inr", "fee_paid_inr",
        "entry_value_inr", "exit_value_inr", "realized_pnl_inr",
        "fy_year", "fifo_match_id",
    ):
        assert col in src, f"tax_events must declare `{col}`"


def test_kill_switch_state_uses_composite_pk() -> None:
    src = _migration_source()
    assert "PRIMARY KEY (user_id, switch_name)" in src


def test_fx_rates_uses_composite_pk() -> None:
    src = _migration_source()
    assert "PRIMARY KEY (pair, ts)" in src


def test_postgres_branch_uses_jsonb_for_payload_columns() -> None:
    src = _migration_source()
    # gate_snapshot, payload, reasoning all use JSONB on Postgres
    assert "gate_snapshot JSONB" in src
    assert "payload JSONB NOT NULL" in src
    assert "reasoning JSONB" in src


def test_sqlite_branch_uses_text_for_jsonb_columns() -> None:
    src = _migration_source()
    # SQLite gate_snapshot / payload / reasoning fall back to TEXT
    assert "gate_snapshot TEXT" in src
    assert "payload TEXT NOT NULL" in src


def test_live_trades_open_positions_partial_index_exists() -> None:
    """Performance: queries for open positions filter on closed_at IS NULL."""
    src = _migration_source()
    assert "live_trades_open_positions_idx" in src
    assert "WHERE closed_at IS NULL" in src


def test_telegram_signals_response_check_enumerates_four_states() -> None:
    src = _migration_source()
    for state in ("approved", "skipped", "timeout", "error"):
        assert f"'{state}'" in src


def test_hardware_confirms_method_check_only_totp_or_telegram() -> None:
    src = _migration_source()
    assert "method IN ('totp', 'telegram')" in src


def test_migration_has_both_dialect_branches() -> None:
    src = _migration_source()
    assert 'dialect == "postgresql"' in src
    assert "else:" in src  # SQLite fallback
