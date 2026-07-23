"""Parquet export of recent OHLCV/predictions/shadow_trades for ML training.

Spec §5.1 — runs nightly via cron; writes to a date-stamped local directory
under ``/app/data/ml-exports/<YYYY-MM-DD>/`` (or any caller-specified directory).

Tables exported:

* ``predictions`` — including the new SP-1 ``ghost_*`` columns + ``model_checkpoint_id``.
* ``shadow_trades`` — used to label which predictions resulted in wins.
* ``ohlcv_1h`` — raw bars, for retraining input. Best-effort: if no such table
  exists in the dev DB, an empty Parquet is written so downstream consumers
  always see all three files.

Each parquet file uses snappy compression. The accompanying ``manifest.json``
carries the sha256 of each file so downstream consumers can verify integrity
after S3/B2 transit.

KNOWN GAP — B2 upload (Phase A scope):
    The original SP-1 spec assumes an existing ``app/backup/`` B2 sync pipeline
    that picks up whatever lands in ``/app/data/ml-exports/<date>/`` and copies
    it to ``b2://trading-radar-backups/ml-exports/...``. That pipeline does NOT
    exist yet (it was never built in SP-0.5). For Phase A we deliberately stop
    at LOCAL DISK — exporting to ``out_dir`` and writing the manifest. A
    separate B2 upload step is OUT OF SCOPE for Phase A and will be addressed
    in Phase C (when Colab actually needs to pull data) or via a tiny SP-0.8
    backup-pipeline sub-project if it becomes blocking sooner.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import pyarrow.parquet as pq
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ExportFileInfo:
    sha256: str
    rows: int
    bytes: int


@dataclass(frozen=True)
class ExportManifest:
    window_days: int
    created_at: str
    files: dict[str, dict[str, Any]] = field(default_factory=dict)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


async def _select_to_table(
    session: AsyncSession, sql: str, params: dict[str, Any],
) -> pa.Table:
    """Run ``sql`` with ``params`` and return the result as a pyarrow Table.

    If there are zero rows, returns an empty table (no schema). Caller decides
    whether to write a placeholder or skip.
    """
    result = await session.execute(sa.text(sql), params)
    rows = result.mappings().all()
    if not rows:
        return pa.table({})
    cols: dict[str, list[Any]] = {k: [] for k in rows[0].keys()}
    for r in rows:
        for k, v in r.items():
            cols[k].append(v)
    return pa.table(cols)


async def export_recent_to_parquet(
    *,
    session_factory: Callable[[], AsyncSession],
    out_dir: Path,
    days: int = 30,
    now: datetime | None = None,
) -> ExportManifest:
    """Export the last ``days`` days of training-relevant data to ``out_dir``.

    The function ALWAYS writes three Parquet files (creating empty placeholders
    when the corresponding table has no rows or doesn't exist), plus a
    ``manifest.json`` describing each output's sha256, row count, and bytes.

    Returns the in-memory ExportManifest dataclass mirroring what was written
    to ``manifest.json``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # Bind the datetime object directly. The previous comment here
    # claimed ISO strings work in both SQLite (TEXT) and Postgres
    # (TIMESTAMPTZ) — factually true for SQLite (lexicographic on TEXT)
    # but WRONG for Postgres via asyncpg, which strict-binds TIMESTAMPTZ
    # and rejects strings.
    queries: dict[str, tuple[str, dict[str, Any]]] = {
        "predictions_30d.parquet": (
            "SELECT * FROM predictions WHERE ts >= :since ORDER BY ts ASC",
            {"since": since},
        ),
        "shadow_trades_30d.parquet": (
            "SELECT * FROM shadow_trades WHERE closed_at >= :since "
            "ORDER BY closed_at ASC",
            {"since": since},
        ),
        # ohlcv may not exist as a table in dev; fall back to empty Parquet.
        "ohlcv_1h_30d.parquet": (
            "SELECT * FROM ohlcv WHERE ts >= :since AND timeframe = '1h' "
            "ORDER BY ts ASC",
            {"since": since},
        ),
    }

    files: dict[str, dict[str, Any]] = {}
    async with session_factory() as session:
        for filename, (sql, params) in queries.items():
            try:
                table = await _select_to_table(session, sql, params)
            except Exception:  # noqa: BLE001 — best-effort: missing table is OK
                table = pa.table({})
                # Per-table failures don't poison subsequent queries; rollback
                # so the next SELECT runs in a clean transaction.
                await session.rollback()
            target = out_dir / filename
            if table.num_rows == 0:
                # Write an empty parquet with a marker schema so downstream
                # consumers always find a file at the expected path.
                pq.write_table(
                    pa.table({"_empty": pa.array([], type=pa.int8())}),
                    target,
                    compression="snappy",
                )
            else:
                pq.write_table(table, target, compression="snappy")
            files[filename] = {
                "sha256": _sha256_of(target),
                "rows": table.num_rows,
                "bytes": target.stat().st_size,
            }

    manifest = ExportManifest(
        window_days=days,
        created_at=now.isoformat(),
        files=files,
    )
    (out_dir / "manifest.json").write_text(json.dumps(asdict(manifest), indent=2))
    return manifest
