"""SP-4 Phase A1: migration 0015 rl_checkpoints + brain_decisions.

File-scan tests that match the conventions established by
``test_migration_0012.py``: load the migration module by file path,
verify revision chaining + table/column declarations + audit-chain
columns + dialect-aware DDL branches.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2026_05_07_0015_rl_brain_tables.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_sp4_migration_0015", _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_source() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_chain_links_to_0014_intermarket() -> None:
    module = _load_migration()
    assert module.revision == "0015_rl_brain"
    assert module.down_revision == "0014_intermarket"


def test_migration_creates_two_tables() -> None:
    src = _migration_source()
    # Table appears twice (postgres + sqlite branches), but both branches
    # create the same set of tables.
    assert src.count("CREATE TABLE rl_checkpoints") == 2
    assert src.count("CREATE TABLE brain_decisions") == 2


def test_migration_drops_both_tables_in_downgrade() -> None:
    src = _migration_source()
    assert "DROP TABLE IF EXISTS rl_checkpoints" in src
    assert "DROP TABLE IF EXISTS brain_decisions" in src


def test_rl_checkpoints_columns_match_spec_section_3_2() -> None:
    """Spec sec 3.2 — required columns on rl_checkpoints."""
    src = _migration_source()
    for col in (
        "model_name",
        "version",
        "checkpoint_uri",
        "sha256",
        "trained_at",
        "train_data_window",
        "eval_results",
        "is_active",
        "activated_at",
        "deactivated_at",
        "notes",
    ):
        assert col in src, f"rl_checkpoints must declare `{col}`"


def test_brain_decisions_carries_audit_chain_columns() -> None:
    """Hash-chain columns are required for SP-7 verify_chain integration."""
    src = _migration_source()
    assert "prev_hash" in src
    assert "row_hash" in src


def test_brain_decisions_action_check_constraint_enumerates_five_actions() -> None:
    """All 5 SP-4 discrete actions must appear in the CHECK constraint."""
    src = _migration_source()
    for action in (
        "LONG_FULL", "LONG_HALF", "FLAT", "SHORT_HALF", "SHORT_FULL",
    ):
        assert action in src, f"action constraint missing `{action}`"


def test_migration_has_postgres_branch() -> None:
    src = _migration_source()
    assert 'dialect == "postgresql"' in src


def test_postgres_branch_uses_jsonb_for_observation() -> None:
    src = _migration_source()
    # The Postgres branch should use JSONB; SQLite branch uses TEXT.
    assert "JSONB" in src
    assert "observation JSONB NOT NULL" in src


def test_postgres_branch_uses_partial_unique_index_for_active() -> None:
    src = _migration_source()
    assert "rl_checkpoints_one_active_idx" in src
    assert "WHERE is_active = TRUE" in src


def test_sqlite_branch_uses_text_for_jsonb_columns() -> None:
    src = _migration_source()
    assert "observation TEXT NOT NULL" in src


def test_unique_constraint_on_model_name_version_pair() -> None:
    src = _migration_source()
    assert src.count("UNIQUE (model_name, version)") == 2  # both dialects


def test_brain_decisions_index_on_symbol_ts() -> None:
    src = _migration_source()
    assert "brain_decisions_symbol_ts_idx" in src
    assert "(symbol, ts DESC)" in src
