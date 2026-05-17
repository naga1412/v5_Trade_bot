"""G1 — Hold/TP scaling by mtf_agreement (PR3 Phase 5.5).

Pure lookup helper. Worker calls `effective_hold_tp(...)` at trade-open
time to convert (timeframe, mtf_agreement) into (timeout_bars,
tp_multiplier). Stop-loss is INVARIANT under scaling (per-trade risk
constant); only TP distance widens and timeout extends with conviction.

Default OFF (HOLD_TP_SCALING_ENABLED=False). When OFF the worker uses
the per-TF baseline (TIMEOUT_BARS_PER_TF[tf]) directly and the recorded
hold_scaling_factor + hold_timeout_bars stay NULL on shadow_trades.

Fail-open contract (spec §4.6b):
  - mtf_agreement is None → (baseline, 1.0×) + no warning (PR1 cold-cache
    fall-through; PR2 gate already passed here).
  - mtf_agreement below the lowest table key → (baseline, 1.0×) + WARN
    (shouldn't happen — PR2 MTF_MIN_AGREEMENT_1H gate blocks first).
  - mtf_agreement above the highest table key → cap at the highest key
    (no extrapolation beyond table; future spec tuning extends the table).
  - Unknown timeframe → KeyError (fail-loud; matches exit_monitor's
    TIMEOUT_BARS_PER_TF contract; new TFs require an explicit entry).
"""
from __future__ import annotations

import logging

from app.shadow.exit_monitor import TIMEOUT_BARS_PER_TF


log = logging.getLogger(__name__)


def effective_hold_tp(
    *,
    timeframe: str,
    mtf_agreement: int | None,
    table: dict[int, tuple[int, float]],
) -> tuple[int, float]:
    """Compute the scaled (timeout_bars, tp_multiplier) for a signal.

    Multiplier semantics: the table is indexed against the 1h baseline
    (its lowest-key entry, conventionally `{3: (24, 1.0), ...}`). For
    non-1h TFs the timeout multiplier `(table_bars / 1h_baseline_bars)`
    is applied against that TF's baseline `TIMEOUT_BARS_PER_TF[tf]`,
    producing equal *wall-clock* scaling across TFs. The tp_multiplier
    is TF-invariant.

    Example: with the default table and tf="15m":
      - mtf_agreement=3 → (96, 1.0)    [baseline; no scaling]
      - mtf_agreement=4 → (192, 1.25)  [96 × (48/24) = 192]
      - mtf_agreement=5 → (384, 1.5)
      - mtf_agreement=6 → (672, 2.0)

    Raises:
      - KeyError if ``timeframe`` is not in TIMEOUT_BARS_PER_TF.
    """
    tf_baseline_bars = TIMEOUT_BARS_PER_TF[timeframe]  # KeyError → fail-loud

    if mtf_agreement is None:
        return (tf_baseline_bars, 1.0)

    sorted_keys = sorted(table.keys())
    lowest, highest = sorted_keys[0], sorted_keys[-1]
    if mtf_agreement < lowest:
        log.warning(
            "effective_hold_tp: mtf_agreement=%d below table (min=%d); "
            "falling back to baseline. PR2 MTF gate should have blocked.",
            mtf_agreement, lowest,
        )
        return (tf_baseline_bars, 1.0)
    agreement = mtf_agreement if mtf_agreement <= highest else highest

    table_bars, tp_mult = table[agreement]
    bars_baseline_1h = table[lowest][0]  # the 1h baseline (e.g. 24)
    bars = int(tf_baseline_bars * (table_bars / bars_baseline_1h))
    return (bars, tp_mult)
