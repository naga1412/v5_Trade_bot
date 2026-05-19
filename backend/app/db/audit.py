import hashlib
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

GENESIS_HASH: str = "0" * 64

# Per-table whitelist of columns that contribute to the audit hash chain.
# Adding a column to a chained table is a NO-OP for the chain UNLESS its
# name is also added below. New analytics/recording-only columns belong
# in NON_HASHED_ALLOW_LIST instead (forces conscious decision per column).
#
# Initial values MUST equal exactly the union of keys that current call
# sites pass to insert_with_chain. Verified by tests/db/test_audit_replay_identity.py.
HASH_PAYLOAD_COLUMNS: dict[str, frozenset[str]] = {
    "predictions": frozenset({
        "user_id", "symbol", "timeframe", "ts", "layer_scores",
        "final_score", "direction", "confidence", "inputs_hash",
        "model_version", "cold_start",
        "ghost_open", "ghost_high", "ghost_low", "ghost_close",
        "ghost_p5_low", "ghost_p95_high", "ghost_uncertainty",
        "model_checkpoint_id",
    }),
    "shadow_trades": frozenset({
        "user_id", "symbol", "timeframe", "direction",
        "entry_price", "stop_loss", "take_profit",
        "position_size_usdt", "entry_score", "entry_confidence",
        "layer_scores", "entry_atr",
        "exit_price", "exit_reason", "pnl_pct", "pnl_usdt",
        "bars_held", "opened_at", "closed_at", "inputs_hash",
        "model_version", "signal_id",
    }),
    "live_trades": frozenset({
        "user_id", "symbol", "direction",
        "margin_usdt", "leverage", "position_value_usdt",
        "entry_price", "stop_loss", "take_profit",
        "binance_order_id", "opened_at",
        "mode_at_open", "approved_via", "reasoning", "inputs_hash",
    }),
    "paper_trades": frozenset({
        "symbol", "direction",
        "entry_price", "exit_price", "stop_loss", "take_profit",
        "position_size", "opened_at", "closed_at",
        "pnl_pct", "max_drawdown_during", "bars_held",
        "exit_reason", "reasoning", "model_version",
    }),
    # Additional hash-chained tables discovered at call-site audit.
    # Each must be registered here to satisfy the fail-secure contract.
    "brain_decisions": frozenset({
        "ts", "symbol", "checkpoint_id", "observation",
        "action", "action_logits", "value_estimate", "smoothed_action",
    }),
    "tax_events": frozenset({
        "trade_id", "user_id", "symbol", "direction", "quantity",
        "entry_price", "exit_price", "entry_value_inr", "exit_value_inr",
        "realized_pnl_inr", "tds_owed_inr", "fee_paid_inr",
        "leverage", "exchange", "fy_year", "closed_at", "fifo_match_id",
    }),
    "mode_change_log": frozenset({
        "user_id", "old_mode", "new_mode", "triggered_by",
        "reason", "gate_snapshot", "changed_at",
    }),
    "symbol_performance_snapshots": frozenset({
        "symbol", "window_start", "window_end",
        "trades_count", "win_rate", "sharpe", "allowed",
        "computed_at",
    }),
}

# Per-table allowlist of columns that exist on the table but are NOT
# part of the audit hash chain. Recording-only analytics columns live
# here. Test test_audit_whitelist_consistency.py fails if a column
# appears on the schema but is in neither set (requires Postgres + a
# migrated schema; the test skips on SQLite/in-memory). Run the test
# against the migrated test DB to validate.
NON_HASHED_ALLOW_LIST: dict[str, frozenset[str]] = {
    "predictions": frozenset({
        "id", "prev_hash", "row_hash",  # chain metadata + autoincrement PK
        # PR1 recording-only columns (added in alembic 2026_05_16_XXXX):
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d",
        "funding_directional_adj",
    }),
    "shadow_trades": frozenset({
        "id", "prev_hash", "row_hash",
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d",
        "funding_directional_adj",
        # PR3 G1 (alembic 2026_05_18_0021_pr3_shadow_per_tf): recording-only
        # scaling fields. Populated by shadow_worker when
        # HOLD_TP_SCALING_ENABLED=True; NULL otherwise. Never hashed —
        # they describe POST-decision execution metadata, not the signal
        # inputs whose integrity the chain protects.
        "hold_scaling_factor", "hold_timeout_bars",
    }),
    "live_trades": frozenset({
        "id", "prev_hash", "row_hash",
        "timeframe",  # added in PR1, not part of chain on live_trades
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d",
        "funding_directional_adj",
        # PR3 G1: reserved columns. PR3 itself does NOT populate these on
        # live_trades; a future PR wires the auto-path + telegram-approve
        # path. Classifying as NOT hashed keeps them out of the chain
        # whether or not a future PR ever fills them.
        "hold_scaling_factor", "hold_timeout_bars",
        # Trade-closure columns. These are written by close-trade UPDATEs,
        # never present at insert_with_chain time. Classifying as hashed
        # would hash NULLs at open (false tamper-detection on every close).
        "binance_position_id", "closed_at", "exit_price", "exit_reason",
        "fees_paid_usdt", "funding_paid_usdt", "liquidation_price",
        "pnl_pct", "pnl_usdt",
    }),
    "paper_trades": frozenset({
        "id", "prev_hash", "row_hash",
        # user_id present but unclassified pre-PR1. paper_trades has 0 rows
        # in prod and no active writers; classifying as NOT hashed matches
        # current runtime behavior exactly (the fail-secure whitelist never
        # included it, so no hashing was ever happening). See FU-13 for the
        # delete-vs-revive decision.
        "user_id",
    }),
    "brain_decisions": frozenset({
        "id", "prev_hash", "row_hash",
    }),
    "tax_events": frozenset({
        "id", "prev_hash", "row_hash",
    }),
    "mode_change_log": frozenset({
        "id", "prev_hash", "row_hash",
    }),
    "symbol_performance_snapshots": frozenset({
        "id", "prev_hash", "row_hash", "inputs_hash",
    }),
}


def canonical_row_json(row: dict[str, Any]) -> str:
    """Canonical JSON serialization for hashing.

    sort_keys=True and compact separators give a deterministic byte
    representation so the same row always hashes to the same value.
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    payload = (prev_hash + canonical_row_json(row)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert_table_registered(table: str) -> frozenset[str]:
    """Fail-secure validation that ``table`` is a registered hash-chained
    table. Returns the whitelist for that table on success.

    Per Correction 1: unknown table raises ValueError loudly — any caller
    of insert_with_chain for a non-whitelisted table is a bug we want
    surfaced, not silently hashed-and-forgotten.
    """
    whitelist = HASH_PAYLOAD_COLUMNS.get(table)
    if whitelist is None:
        raise ValueError(
            f"Table {table!r} not in HASH_PAYLOAD_COLUMNS. "
            f"Hash-chained tables must be explicitly registered."
        )
    return whitelist


def _filter_for_hash(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys not in HASH_PAYLOAD_COLUMNS[table] before hashing.

    Raises ValueError if ``table`` is not a registered hash-chained table.
    """
    whitelist = _assert_table_registered(table)
    return {k: v for k, v in payload.items() if k in whitelist}


async def _last_row_hash(session: AsyncSession, table: str) -> str:
    result = await session.execute(
        sa.text(f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1")
    )
    row = result.first()
    return row.row_hash if row else GENESIS_HASH


async def insert_with_chain(
    session: AsyncSession, table: str, payload: dict[str, Any]
) -> str:
    """Insert payload + computed prev_hash/row_hash. Returns row_hash.

    Only keys in ``HASH_PAYLOAD_COLUMNS[table]`` contribute to the row hash.
    Columns not in the whitelist (e.g. recording-only analytics) are
    written to the DB but excluded from the chain. Fail-secure contract:
      - forgotten column → "not tamper-evident", visible in consistency test
      - unknown table → raises ValueError (per Correction 1)
    """
    hashable = _filter_for_hash(table, payload)  # raises on unknown table BEFORE any DB I/O
    prev = await _last_row_hash(session, table)
    new_hash = compute_row_hash(prev, hashable)
    full = {**payload, "prev_hash": prev, "row_hash": new_hash}
    cols = ", ".join(full.keys())
    params = ", ".join(f":{k}" for k in full.keys())
    await session.execute(
        sa.text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), full
    )
    return new_hash
