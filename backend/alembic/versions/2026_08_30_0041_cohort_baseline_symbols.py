"""cohort_baseline_symbols -- frozen pre-Phase-4 fleet identity (2026-08-30)

Operator ruling on the cohort-tag defect (2026-08-30): the classifier
that assigns symbol_source must become a PURE function of identity --
"in frozen baseline -> established_top20; not in baseline, no spot pair
-> futures_poll; not in baseline, has spot pair -> liquidity_added_spot"
-- with no memory of prior live_fleet_universe snapshots (the source of
the original defect: legacy_top20 only populates on the very first
cold-start refresh, and the open-position-rescue path was hardcoding
established_top20 as a fallback default, fabricating lineage for
symbols with none).

This table is that frozen identity set, persisted as a DB row per the
operator's explicit instruction ("a DB row, not a code constant that
can silently drift"). Reconstructed from OBSERVED pre-cutover activity,
not from asset_universe.rank (that table is the OLD selector's INPUT --
a top-30 ranking; ranks 21-30 were ranked but never actually streamed
by ws_keepalive, so presence there does not mean the symbol was ever
live). The only real record of what was actually streamed and scored
pre-cutover is the predictions table itself.

Definition (operator's locked, pre-registered rule): a symbol qualifies
if it has predictions on >=2 distinct calendar days in the 30-day
window ending at cold-start (2026-08-19 ~11:26:40 UTC) -- "held through
at least one daily refresh boundary," not merely a same-day flicker --
MINUS 6 confirmed stablecoin/synthetic exclusions (UUSDT/SKHYBUSDT/
SOXLBUSDT/KORUBUSDT/SNXXBUSDT/EURIUSDT: pegged/synthetic instruments
that structurally cannot move, which would bias the control arm's
measured performance upward and make expansion look artificially
better by comparison -- the wrong direction to err in for a reversal
criterion).

73 symbols. Verified via a dedicated read-only ops-debug probe
(baseline-final-recount) dispatched against real staging data; every
row here is directly traceable to that run's output, not hand-typed
from memory.

Revision ID: 0041_cohort_baseline_symbols
Revises: 0040_shadow_open_pos_cols
Create Date: 2026-08-30
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0041_cohort_baseline"
down_revision: str | None = "0040_shadow_open_pos_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (symbol, distinct_predictions_days, pred_n) -- the two count columns are
# provenance only (what the baseline-final-recount probe measured), not
# live data; they do not change after this migration runs.
_BASELINE_SYMBOLS: list[tuple[str, int, int]] = [
    ("BTCUSDT", 31, 719),
    ("BNBUSDT", 30, 677),
    ("DOGEUSDT", 30, 654),
    ("ETHUSDT", 30, 677),
    ("SOLUSDT", 30, 677),
    ("TRXUSDT", 30, 677),
    ("XRPUSDT", 30, 677),
    ("ZECUSDT", 30, 677),
    ("BANKUSDT", 25, 492),
    ("NEARUSDT", 22, 323),
    ("ADAUSDT", 18, 346),
    ("UNIUSDT", 16, 258),
    ("SUIUSDT", 14, 159),
    ("DEXEUSDT", 13, 260),
    ("PUMPUSDT", 12, 165),
    ("AVAXUSDT", 11, 125),
    ("EULUSDT", 11, 204),
    ("HOMEUSDT", 11, 120),
    ("MUBUSDT", 11, 191),
    ("AAVEUSDT", 10, 119),
    ("ACEUSDT", 10, 167),
    ("ALLOUSDT", 10, 146),
    ("BICOUSDT", 10, 174),
    ("ERAUSDT", 10, 157),
    ("TUTUSDT", 10, 182),
    ("WLDUSDT", 10, 102),
    ("AEROUSDT", 9, 189),
    ("KAITOUSDT", 9, 76),
    ("LINKUSDT", 9, 146),
    ("MMTUSDT", 9, 159),
    ("PEPEUSDT", 9, 134),
    ("BABYUSDT", 8, 167),
    ("COTIUSDT", 8, 147),
    ("REUSDT", 8, 146),
    ("VANAUSDT", 8, 148),
    ("ENAUSDT", 7, 96),
    ("HEIUSDT", 7, 130),
    ("MIRAUSDT", 7, 100),
    ("OPNUSDT", 7, 102),
    ("ZAMAUSDT", 7, 142),
    ("KITEUSDT", 6, 122),
    ("EPICUSDT", 5, 72),
    ("ESPUSDT", 5, 63),
    ("GIGGLEUSDT", 5, 91),
    ("HEMIUSDT", 5, 60),
    ("SHIBUSDT", 5, 65),
    ("CRCLBUSDT", 4, 47),
    ("HFTUSDT", 4, 82),
    ("LTCUSDT", 4, 43),
    ("ONDOUSDT", 4, 48),
    ("PLUMEUSDT", 4, 60),
    ("SYNUSDT", 4, 47),
    ("TSTUSDT", 4, 72),
    ("WLFIUSDT", 4, 57),
    ("BCHUSDT", 3, 24),
    ("BMTUSDT", 3, 48),
    ("MUBARAKUSDT", 3, 48),
    ("PROMUSDT", 3, 36),
    ("RIFUSDT", 3, 57),
    ("TAOUSDT", 3, 25),
    ("TOWNSUSDT", 3, 42),
    ("VICUSDT", 3, 45),
    ("XPLUSDT", 3, 59),
    ("ZBTUSDT", 3, 59),
    ("1000SATSUSDT", 2, 24),
    ("COWUSDT", 2, 23),
    ("GRAMUSDT", 2, 27),
    ("HOLOUSDT", 2, 12),
    ("LAUSDT", 2, 24),
    ("NILUSDT", 2, 23),
    ("ROBOUSDT", 2, 23),
    ("SAGAUSDT", 2, 24),
    ("TLMUSDT", 2, 18),
]

assert len(_BASELINE_SYMBOLS) == 73, (
    f"expected 73 baseline symbols, got {len(_BASELINE_SYMBOLS)} -- "
    "this migration's seed data must match baseline-final-recount's "
    "verified output exactly; do not hand-edit the count"
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE cohort_baseline_symbols (
            symbol TEXT PRIMARY KEY,
            pred_distinct_days INTEGER NOT NULL,
            pred_n INTEGER NOT NULL,
            frozen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    for symbol, distinct_days, pred_n in _BASELINE_SYMBOLS:
        op.execute(
            "INSERT INTO cohort_baseline_symbols "
            "(symbol, pred_distinct_days, pred_n) VALUES "
            f"('{symbol}', {distinct_days}, {pred_n});"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cohort_baseline_symbols;")
