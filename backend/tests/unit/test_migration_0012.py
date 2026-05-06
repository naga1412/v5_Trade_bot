"""SP-7 Phase A2: migration 0012 backtests + hyperopt_studies + backup_runs.

This test exercises the migration module shape (revision IDs, table names,
SQLite-friendliness constraints from the SP-7 plan):
- chains correctly off 0011_trap_enabled
- creates the three new tables
- avoids GENERATED columns and partial unique indexes (cross-cutting
  requirement: schema must be mirrorable into the SQLite test conftest
  without features SQLite cannot express).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2026_05_05_0012_backtests_hyperopt_backups.py"
)


def _load_migration() -> ModuleType:
    """Load migration 0012 by file path.

    The file name starts with a digit (`2026_...`), so the module is not
    importable via importlib.import_module — we use spec_from_file_location
    instead. Alembic itself loads versions the same way.
    """
    spec = importlib.util.spec_from_file_location(
        "_sp7_migration_0012", _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_source() -> str:
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_chain_links_to_0011_trap_enabled() -> None:
    """down_revision points at 0011_trap_enabled; revision id matches plan."""
    module = _load_migration()
    assert module.revision == "0012_backtests_hyperopt_backups"
    assert module.down_revision == "0011_trap_enabled"


def test_migration_creates_three_new_tables() -> None:
    src = _migration_source()
    assert "CREATE TABLE backtests" in src
    assert "CREATE TABLE hyperopt_studies" in src
    assert "CREATE TABLE backup_runs" in src


def test_migration_drops_three_tables_in_downgrade() -> None:
    src = _migration_source()
    assert "DROP TABLE IF EXISTS backtests" in src
    assert "DROP TABLE IF EXISTS hyperopt_studies" in src
    assert "DROP TABLE IF EXISTS backup_runs" in src


def test_migration_avoids_generated_columns() -> None:
    """SP-7 cross-cutting rule: no GENERATED columns (SQLite cannot mirror)."""
    src = _migration_source()
    assert not re.search(r"\bGENERATED\b", src, re.IGNORECASE), (
        "Migration must not use GENERATED columns "
        "(SP-7 cross-cutting requirement: SQLite-friendly)."
    )


def test_migration_avoids_partial_unique_indexes() -> None:
    """SP-7 cross-cutting rule: no partial unique indexes (SQLite cannot mirror)."""
    src = _migration_source()
    pattern = re.compile(
        r"CREATE\s+UNIQUE\s+INDEX[\s\S]*?WHERE",
        re.IGNORECASE,
    )
    assert not pattern.search(src), (
        "Migration must not use partial unique indexes "
        "(SP-7 cross-cutting requirement: SQLite-friendly)."
    )


def test_backtests_table_has_required_columns() -> None:
    """Spec §4.1: backtests carries metrics + params_hash + status enum."""
    src = _migration_source()
    for column in (
        "params_hash TEXT NOT NULL",
        "win_rate DOUBLE PRECISION",
        "profit_factor DOUBLE PRECISION",
        "sharpe DOUBLE PRECISION",
        "max_drawdown DOUBLE PRECISION",
        "equity_curve_uri TEXT",
        "trade_log_uri TEXT",
    ):
        assert column in src, f"backtests must declare `{column}`"


def test_backup_runs_status_check_matches_spec() -> None:
    """Spec §4.3: backup_type CHECK enumerates the three known kinds."""
    src = _migration_source()
    for kind in ("hourly_dump", "nightly_basebackup", "recovery_rehearsal"):
        assert kind in src, f"backup_runs.backup_type CHECK missing `{kind}`"


def test_hyperopt_studies_uses_tstzrange_window_columns() -> None:
    """Spec §4.2: train_window + val_window are TSTZRANGE in Postgres."""
    src = _migration_source()
    assert "train_window TSTZRANGE NOT NULL" in src
    assert "val_window TSTZRANGE NOT NULL" in src
