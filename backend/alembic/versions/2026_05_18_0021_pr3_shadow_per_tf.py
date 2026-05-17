"""PR3: shadow_cooldowns + shadow_open_positions become TF-aware, plus G1 scaling columns.

Two related changes shipped in one migration:

1. **TF-aware shadow state** (B4 + B4-OPEN per spec):
   - `shadow_cooldowns` PK extended from `(user_id, symbol)` to `(user_id, symbol, timeframe)`.
   - `shadow_open_positions` UNIQUE changed from `(symbol)` to `(symbol, timeframe)`.
   - Both use the 4-step pattern (add nullable → backfill `'1h'` → SET NOT NULL → SET DEFAULT `'1h'`)
     proven on PR1's live_trades.timeframe migration. The schema is fully online for concurrent writers.

2. **G1 Hold/TP scaling recording columns** (spec §4.6b):
   - `shadow_trades`: add `hold_scaling_factor REAL NULL` + `hold_timeout_bars SMALLINT NULL`.
     PR3 worker populates these when `HOLD_TP_SCALING_ENABLED=True`; NULL otherwise.
   - `live_trades`: same two columns. PR3 reserves them so a future PR can wire the auto-path
     populate without another schema migration. PR3 itself does NOT populate live_trades.

Raw SQL throughout — alembic's autogenerate doesn't handle PK / UNIQUE rewrites reliably.

Row counts at migration time:
  shadow_cooldowns:        ~10 (low-tens)
  shadow_open_positions:   ≤ 30 (one per symbol in narrow path)
  shadow_trades:           ~30 (recording-only fields added, no backfill of values needed)
  live_trades:             ~5 (reserve columns only; no values populated yet)

Downgrade is fully reversible; the round-trip is exercised by
`tests/db/test_pr3_migration_downgrade.py` per FU-10 anticipation.

Revision ID: 0021_pr3_shadow_per_tf
Revises: 0020_pr1_record_only_columns
Create Date: 2026-05-18
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0021_pr3_shadow_per_tf"
down_revision: str | None = "0020_pr1_record_only_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1a. shadow_cooldowns: extend PK to include timeframe -------------
    op.execute("ALTER TABLE shadow_cooldowns ADD COLUMN timeframe VARCHAR(8) NULL;")
    op.execute("UPDATE shadow_cooldowns SET timeframe = '1h' WHERE timeframe IS NULL;")
    op.execute("ALTER TABLE shadow_cooldowns ALTER COLUMN timeframe SET NOT NULL;")
    op.execute("ALTER TABLE shadow_cooldowns ALTER COLUMN timeframe SET DEFAULT '1h';")
    op.execute("ALTER TABLE shadow_cooldowns DROP CONSTRAINT shadow_cooldowns_pkey;")
    op.execute(
        "ALTER TABLE shadow_cooldowns "
        "ADD CONSTRAINT shadow_cooldowns_pkey PRIMARY KEY (user_id, symbol, timeframe);"
    )

    # --- 1b. shadow_open_positions: replace symbol UNIQUE with (symbol, tf) UNIQUE
    op.execute("ALTER TABLE shadow_open_positions ADD COLUMN timeframe VARCHAR(8) NULL;")
    op.execute(
        "UPDATE shadow_open_positions SET timeframe = '1h' WHERE timeframe IS NULL;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions ALTER COLUMN timeframe SET NOT NULL;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions ALTER COLUMN timeframe SET DEFAULT '1h';"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "DROP CONSTRAINT shadow_open_positions_symbol_key;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "ADD CONSTRAINT shadow_open_positions_symbol_tf_key UNIQUE (symbol, timeframe);"
    )

    # --- 2. G1 Hold/TP scaling recording columns (spec §4.6b) -------------
    # NULL when scaling is OFF; populated when HOLD_TP_SCALING_ENABLED=True.
    # Recording-only — out of HASH_PAYLOAD_COLUMNS per policy; the audit
    # verifier's NON_HASHED_ALLOW_LIST gains these in app/db/audit.py.
    op.execute(
        "ALTER TABLE shadow_trades ADD COLUMN hold_scaling_factor REAL NULL;"
    )
    op.execute(
        "ALTER TABLE shadow_trades ADD COLUMN hold_timeout_bars SMALLINT NULL;"
    )
    # live_trades: reserved for forward compat. PR3 does NOT populate;
    # future PR wires the auto-path + telegram-approve path populate.
    op.execute(
        "ALTER TABLE live_trades ADD COLUMN hold_scaling_factor REAL NULL;"
    )
    op.execute(
        "ALTER TABLE live_trades ADD COLUMN hold_timeout_bars SMALLINT NULL;"
    )


def downgrade() -> None:
    # Reverse order. Per the plan's Phase 5.5.1: G1 columns dropped first,
    # then the PR3 TF-aware changes. Any 15m rows persisted post-deploy
    # are lost on downgrade (acceptable — operator's explicit decision).

    # --- 2. Drop G1 columns ------------------------------------------------
    op.execute("ALTER TABLE live_trades DROP COLUMN hold_timeout_bars;")
    op.execute("ALTER TABLE live_trades DROP COLUMN hold_scaling_factor;")
    op.execute("ALTER TABLE shadow_trades DROP COLUMN hold_timeout_bars;")
    op.execute("ALTER TABLE shadow_trades DROP COLUMN hold_scaling_factor;")

    # --- 1b. shadow_open_positions: restore symbol UNIQUE -----------------
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "DROP CONSTRAINT shadow_open_positions_symbol_tf_key;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "ADD CONSTRAINT shadow_open_positions_symbol_key UNIQUE (symbol);"
    )
    op.execute("ALTER TABLE shadow_open_positions DROP COLUMN timeframe;")

    # --- 1a. shadow_cooldowns: restore (user_id, symbol) PK ---------------
    op.execute("ALTER TABLE shadow_cooldowns DROP CONSTRAINT shadow_cooldowns_pkey;")
    op.execute(
        "ALTER TABLE shadow_cooldowns "
        "ADD CONSTRAINT shadow_cooldowns_pkey PRIMARY KEY (user_id, symbol);"
    )
    op.execute("ALTER TABLE shadow_cooldowns DROP COLUMN timeframe;")
