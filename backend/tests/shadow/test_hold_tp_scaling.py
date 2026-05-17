"""G1 — Hold/TP scaling lookup tests (Phase 5.5.3).

Pure helper, no I/O. Covers the lookup contract per spec §4.6b:
  - table lookup returns the right (bars, tp_mult) per agreement
  - mtf_agreement=None → baseline (PR1 fail-open)
  - mtf_agreement < table_min → baseline + WARN
  - mtf_agreement > table_max → cap at highest key
  - 15m TF scales bars against the per-TF baseline (96), not 1h's
  - Unknown TF → KeyError (fail-loud)
"""
from __future__ import annotations

import logging

import pytest

from app.shadow.scaling import effective_hold_tp


_DEFAULT_TABLE: dict[int, tuple[int, float]] = {
    3: (24, 1.0),
    4: (48, 1.25),
    5: (96, 1.5),
    6: (168, 2.0),
}


# --- Lookup per agreement -------------------------------------------------


def test_lookup_per_agreement_returns_table_entry_1h() -> None:
    """Spec §4.6b default table, 1h TF: pass-through against baseline 24."""
    assert effective_hold_tp(
        timeframe="1h", mtf_agreement=3, table=_DEFAULT_TABLE,
    ) == (24, 1.0)
    assert effective_hold_tp(
        timeframe="1h", mtf_agreement=4, table=_DEFAULT_TABLE,
    ) == (48, 1.25)
    assert effective_hold_tp(
        timeframe="1h", mtf_agreement=5, table=_DEFAULT_TABLE,
    ) == (96, 1.5)
    assert effective_hold_tp(
        timeframe="1h", mtf_agreement=6, table=_DEFAULT_TABLE,
    ) == (168, 2.0)


def test_lookup_agreement_3_returns_baseline_no_scaling() -> None:
    """agreement=3 maps to (baseline, 1.0×) — neither timeout nor TP changes."""
    bars, mult = effective_hold_tp(
        timeframe="1h", mtf_agreement=3, table=_DEFAULT_TABLE,
    )
    assert (bars, mult) == (24, 1.0)


# --- 15m TF scaling against per-TF baseline -------------------------------


def test_lookup_15m_scales_against_per_tf_baseline() -> None:
    """For 15m, the bars multiplier (table_bars / 24) is applied to the
    per-TF baseline (TIMEOUT_BARS_PER_TF["15m"]=96). agreement=4 → 192."""
    bars, mult = effective_hold_tp(
        timeframe="15m", mtf_agreement=4, table=_DEFAULT_TABLE,
    )
    assert bars == 192  # 96 × (48/24)
    assert mult == 1.25  # tp multiplier is TF-invariant


def test_lookup_15m_agreement_5() -> None:
    """15m + agreement=5 → 384 bars (96h wall-clock) + 1.5× TP."""
    bars, mult = effective_hold_tp(
        timeframe="15m", mtf_agreement=5, table=_DEFAULT_TABLE,
    )
    assert bars == 384  # 96 × (96/24)
    assert mult == 1.5


def test_lookup_15m_agreement_3_is_pure_baseline() -> None:
    """agreement=3 on 15m: no scaling → 96 bars unchanged."""
    bars, mult = effective_hold_tp(
        timeframe="15m", mtf_agreement=3, table=_DEFAULT_TABLE,
    )
    assert bars == 96
    assert mult == 1.0


# --- Fail-open edge cases -------------------------------------------------


def test_none_agreement_returns_baseline() -> None:
    """mtf_agreement=None (PR1 fail-open) → baseline + no scaling, no warn."""
    bars, mult = effective_hold_tp(
        timeframe="1h", mtf_agreement=None, table=_DEFAULT_TABLE,
    )
    assert (bars, mult) == (24, 1.0)


def test_none_agreement_on_15m_returns_15m_baseline() -> None:
    """None + 15m → 96 bars baseline (not 1h's 24)."""
    bars, mult = effective_hold_tp(
        timeframe="15m", mtf_agreement=None, table=_DEFAULT_TABLE,
    )
    assert (bars, mult) == (96, 1.0)


def test_agreement_below_table_falls_back_with_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """agreement=2 shouldn't reach here (PR2 MTF gate blocks); if it does,
    fall back to baseline + WARN so ops can detect a missing gate."""
    caplog.set_level(logging.WARNING, logger="app.shadow.scaling")
    bars, mult = effective_hold_tp(
        timeframe="1h", mtf_agreement=2, table=_DEFAULT_TABLE,
    )
    assert (bars, mult) == (24, 1.0)
    assert any("below table" in r.message for r in caplog.records)


def test_agreement_above_table_caps_at_highest_key() -> None:
    """agreement=7 caps at the highest table key (6) — no extrapolation.
    Future spec tuning extends the table; G1 itself ships the fixed dict."""
    bars, mult = effective_hold_tp(
        timeframe="1h", mtf_agreement=7, table=_DEFAULT_TABLE,
    )
    # caps at agreement=6 → (168, 2.0)
    assert (bars, mult) == (168, 2.0)


# --- Programming-error fail-loud ------------------------------------------


def test_unknown_tf_raises_keyerror() -> None:
    """Unknown TF → KeyError. Matches the exit_monitor.TIMEOUT_BARS_PER_TF
    contract; new TFs (5m, 4h) require explicit entries in BOTH dicts."""
    with pytest.raises(KeyError):
        effective_hold_tp(
            timeframe="5m", mtf_agreement=4, table=_DEFAULT_TABLE,
        )


def test_empty_table_raises_indexerror() -> None:
    """Defensive: an empty table can't be queried (`sorted_keys[0]` raises).
    This catches a corrupt settings override (e.g. someone sets
    HOLD_TP_SCALING_TABLE={} in env). The worker will see the exception and
    fall back to baseline via its outer try-except — locks that contract."""
    with pytest.raises(IndexError):
        effective_hold_tp(
            timeframe="1h", mtf_agreement=4, table={},
        )
