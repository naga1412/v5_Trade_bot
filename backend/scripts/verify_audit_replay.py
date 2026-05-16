"""Replay-identity verification for all 7 hash-chained tables.

SELF-CONTAINED: defines the whitelist + hash function inline so the
script can run against any deployed prod container (no dependency on
the new audit.py refactor being deployed).

Runs against the live Postgres instance (DATABASE_URL env var). For each
table in HASH_PAYLOAD_COLUMNS:

  1. Discover column types via information_schema (note JSONB columns).
  2. Read last min(100, row_count) rows in id order.
  3. For each row, try 3 hash strategies — JSONB-as-text, asyncpg-
     decoded-then-redumped, raw-asyncpg-decoded — and report which
     (if any) matches the stored row_hash.
  4. Print match/mismatch counts per table; exit 1 on any mismatch.

Designed to run via ops-debug.yml probe — base64-encoded into the YAML
and piped to `python -` inside the backend container.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


GENESIS_HASH: str = "0" * 64

# Inlined from feat/pr1-record-only-foundation backend/app/db/audit.py.
# Keep in sync if the whitelist changes there.
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
}


def compute_row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    """Replica of app.db.audit.compute_row_hash — kept inline so the
    script is self-contained even if audit.py is refactored later.
    """
    canon = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    payload = (prev_hash + canon).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def _discover_jsonb_cols(conn, table: str) -> set[str]:
    """Return the set of JSONB column names on `table`."""
    rows = (await conn.execute(sa.text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :t
          AND udt_name IN ('jsonb', 'json')
        """
    ), {"t": table})).all()
    return {r.column_name for r in rows}


def _strategy_b_redump(
    row_dict: dict[str, Any], jsonb_cols: set[str],
) -> dict[str, Any]:
    """Re-serialize JSONB dict/list values via json.dumps(separators=(",",":")) ."""
    out: dict[str, Any] = {}
    for k, v in row_dict.items():
        if k in jsonb_cols and isinstance(v, (dict, list)):
            out[k] = json.dumps(v, separators=(",", ":"))
        else:
            out[k] = v
    return out


async def _verify_table(conn, table: str) -> dict[str, Any]:
    """Verify chain for one table. Returns result dict."""
    whitelist = HASH_PAYLOAD_COLUMNS[table]
    jsonb_cols = await _discover_jsonb_cols(conn, table)

    row_count = (await conn.execute(
        sa.text(f"SELECT count(*) AS c FROM {table}")
    )).scalar_one()

    if row_count == 0:
        return {
            "table": table, "row_count": 0, "checked": 0,
            "matches_a": 0, "matches_b": 0, "matches_c": 0,
            "mismatches": 0, "jsonb_cols": sorted(jsonb_cols),
            "violations": [],
        }

    # Get all column names so we can ::text-cast the JSONB ones
    all_cols = (await conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"
    ), {"t": table})).all()
    all_col_names = [r.column_name for r in all_cols]

    cols_a = []
    for col in all_col_names:
        if col in jsonb_cols:
            cols_a.append(f"{col}::text AS {col}")
        else:
            cols_a.append(col)
    select_a = ", ".join(cols_a)
    select_bc = ", ".join(all_col_names)

    limit = min(100, row_count)
    rows_a = (await conn.execute(sa.text(
        f"SELECT {select_a} FROM {table} ORDER BY id ASC LIMIT :lim"
    ), {"lim": limit})).all()
    rows_bc = (await conn.execute(sa.text(
        f"SELECT {select_bc} FROM {table} ORDER BY id ASC LIMIT :lim"
    ), {"lim": limit})).all()

    # Chain anchor
    if limit == row_count:
        expected_prev = GENESIS_HASH
    else:
        first_id = rows_a[0].id
        anchor = (await conn.execute(sa.text(
            f"SELECT row_hash FROM {table} WHERE id < :fid "
            f"ORDER BY id DESC LIMIT 1"
        ), {"fid": first_id})).first()
        expected_prev = anchor.row_hash if anchor else GENESIS_HASH

    matches_a = matches_b = matches_c = 0
    violations: list[dict[str, Any]] = []

    for row_a, row_bc in zip(rows_a, rows_bc, strict=True):
        rd_a = dict(row_a._mapping)
        rd_bc = dict(row_bc._mapping)
        filt_a = {k: v for k, v in rd_a.items() if k in whitelist}
        filt_c = {k: v for k, v in rd_bc.items() if k in whitelist}
        filt_b = _strategy_b_redump(filt_c, jsonb_cols)

        hash_a = compute_row_hash(expected_prev, filt_a)
        hash_b = compute_row_hash(expected_prev, filt_b)
        hash_c = compute_row_hash(expected_prev, filt_c)

        stored_hash = rd_bc["row_hash"]
        stored_prev = rd_bc["prev_hash"]
        prev_link_ok = (stored_prev == expected_prev)

        any_match = False
        if prev_link_ok and hash_a == stored_hash:
            matches_a += 1; any_match = True
        if prev_link_ok and hash_b == stored_hash:
            matches_b += 1; any_match = True
        if prev_link_ok and hash_c == stored_hash:
            matches_c += 1; any_match = True

        if not any_match:
            violations.append({
                "row_id": rd_bc["id"],
                "stored_prev": stored_prev,
                "expected_prev_from_chain": expected_prev,
                "prev_link_ok": prev_link_ok,
                "stored_row_hash": stored_hash,
                "hash_a_jsonb_as_text": hash_a,
                "hash_b_redumped": hash_b,
                "hash_c_raw_asyncpg": hash_c,
                "jsonb_cols": sorted(jsonb_cols),
                "non_jsonb_filt_sample": {
                    k: (str(v)[:80] + "…") if v is not None and len(str(v)) > 80 else v
                    for k, v in filt_a.items() if k not in jsonb_cols
                },
            })

        expected_prev = stored_hash  # walk the chain regardless

    return {
        "table": table,
        "row_count": row_count,
        "checked": limit,
        "matches_a": matches_a,
        "matches_b": matches_b,
        "matches_c": matches_c,
        "mismatches": len(violations),
        "jsonb_cols": sorted(jsonb_cols),
        "violations": violations[:3],  # first 3 only
    }


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 2
    engine = create_async_engine(url)
    rows_output: list[dict[str, Any]] = []
    overall_mismatches = 0
    try:
        async with engine.connect() as conn:
            for table in sorted(HASH_PAYLOAD_COLUMNS.keys()):
                try:
                    r = await _verify_table(conn, table)
                except Exception as e:  # noqa: BLE001
                    rows_output.append({
                        "table": table, "error": f"{type(e).__name__}: {e}",
                    })
                    continue
                rows_output.append(r)
                overall_mismatches += r["mismatches"]
    finally:
        await engine.dispose()

    print()
    print(f"{'Table':<18} {'Rows':>5} {'Chk':>4} {'M-A':>4} {'M-B':>4} {'M-C':>4} {'Bad':>4}  JSONB cols")
    print("-" * 100)
    for r in rows_output:
        if "error" in r:
            print(f"{r['table']:<18} ERROR: {r['error']}")
            continue
        print(
            f"{r['table']:<18} "
            f"{r['row_count']:>5} "
            f"{r['checked']:>4} "
            f"{r['matches_a']:>4} "
            f"{r['matches_b']:>4} "
            f"{r['matches_c']:>4} "
            f"{r['mismatches']:>4}  "
            f"{','.join(r['jsonb_cols']) or '-'}"
        )

    has_diagnostics = any(r.get("violations") for r in rows_output)
    if has_diagnostics:
        print()
        print("=== DIAGNOSTICS (first 3 violations per table) ===")
        for r in rows_output:
            if r.get("violations"):
                print(f"\n[{r['table']}]")
                print(json.dumps(r["violations"], indent=2, default=str))

    print()
    print(f"TOTAL_MISMATCHES={overall_mismatches}")
    print("Legend: M-A=hash matches when JSONB read as text; M-B=hash matches after re-json.dumps; M-C=hash matches with raw asyncpg-decoded value")
    return 0 if overall_mismatches == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
