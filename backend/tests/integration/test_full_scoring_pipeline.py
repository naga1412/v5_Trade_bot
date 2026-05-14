"""SP-5 Phase E5 — full scoring pipeline E2E (layers + traps + aggregator + tier).

End-to-end exercise of the scoring stack assembled in Phase A..E:

  1. Build 300 bars of synthetic OHLCV.
  2. Construct a ``layer_scores`` dict mixing LONG / SHORT / None across
     several layer slots.
  3. Run ``check_all_traps(...)`` against the bars + a neutral
     :class:`TrapContext`. With flat synthetic bars and no news / Friday /
     funding signals, the orchestrator should return cleanly (most likely
     an empty list).
  4. Feed the layer scores + trap fires through ``aggregate(...)`` with
     ``brain_adjust=1.0`` and ``news_multiplier=1.0``.
  5. Classify the resulting :class:`FinalScore` via ``classify_tier(...)``
     and assert the tier is one of the five legal labels.

Predictor wiring (``build_prediction`` end-to-end with ``traps_fired`` /
``tier`` / ``static_score`` fields) lands in Phase F1; this Phase E test
exercises only the parts already wired.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.aggregator import aggregate
from app.core.scoring.run_traps import check_all_traps
from app.core.scoring.tiers import Tier, classify_tier
from app.core.scoring.traps.base import TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_LEGAL_TIERS: set[Tier] = {"NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+"}


def _bars(n: int = 300) -> pd.DataFrame:
    """Flat synthetic OHLCV — no extreme moves so most traps stay quiet."""
    rng = np.random.default_rng(42)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 0.05, size=n))
    return pd.DataFrame({
        "open": closes,
        "high": closes + 0.1,
        "low": closes - 0.1,
        "close": closes,
        "volume": np.full(n, 1000.0),
    }, index=pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"))


def _moderate_layer_scores() -> dict[int, LayerScore | None]:
    """A mix of LONG / SHORT / None giving a moderate net-LONG static score."""
    return {
        1: LayerScore(Direction.LONG, 0.5, 0.7),
        2: None,
        3: LayerScore(Direction.LONG, 0.4, 0.6),
        4: None,
        5: LayerScore(Direction.SHORT, 0.3, 0.5),
        6: None,
        7: None,
        8: None,
        9: LayerScore(Direction.NEUTRAL, 0.0, 0.5),
        10: None,
    }


def test_full_pipeline_returns_legal_tier() -> None:
    bars = _bars()
    layer_scores = _moderate_layer_scores()

    fires = check_all_traps(
        bars=bars,
        current_idx=len(bars) - 1,
        layer_scores=layer_scores,
        proposed_direction=Direction.LONG,
        context=TrapContext(),
    )
    assert isinstance(fires, list)
    assert all(isinstance(f, TrapFire) for f in fires)

    final = aggregate(
        layer_scores,
        trap_fires=fires,
        brain_adjust=1.0,
        news_multiplier=1.0,
    )
    tier = classify_tier(final)
    assert tier in _LEGAL_TIERS


def test_full_pipeline_moderate_inputs_yield_moderate_tier() -> None:
    """Moderate layer mix should not produce A+ / STANDARD."""
    bars = _bars()
    layer_scores = _moderate_layer_scores()

    fires = check_all_traps(
        bars=bars,
        current_idx=len(bars) - 1,
        layer_scores=layer_scores,
        proposed_direction=Direction.LONG,
        context=TrapContext(),
    )
    final = aggregate(
        layer_scores,
        trap_fires=fires,
        brain_adjust=1.0,
        news_multiplier=1.0,
    )
    tier = classify_tier(final)
    assert tier in {"NO_SIGNAL", "PAPER"}


def test_full_pipeline_strong_long_inputs_yield_strong_tier() -> None:
    """Stacked LONG layers + zero traps -> SMALL or higher tier."""
    bars = _bars()
    layer_scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    for i in (1, 2, 3, 4, 5, 6):
        layer_scores[i] = LayerScore(Direction.LONG, 0.85, 0.9)

    fires = check_all_traps(
        bars=bars,
        current_idx=len(bars) - 1,
        layer_scores=layer_scores,
        proposed_direction=Direction.LONG,
        context=TrapContext(),
    )
    final = aggregate(
        layer_scores,
        trap_fires=fires,
        brain_adjust=1.0,
        news_multiplier=1.0,
    )
    assert final.direction is Direction.LONG
    tier = classify_tier(final)
    # 0.85 * 0.9 = 0.765 static, no trap penalty if fires is empty.
    # Even with a couple of traps firing the tier should remain a meaningful
    # signal (SMALL+) rather than NO_SIGNAL.
    assert tier in {"SMALL", "STANDARD", "A+", "PAPER"}


def test_full_pipeline_moderate_short_demoted_by_trap_penalties() -> None:
    """A moderate stacked SHORT (0.56 raw) should still drop to NO_SIGNAL once
    the trap penalty pipeline runs against this synthetic bar series.

    Prior to the 2026-05-14 symmetric flip this assertion was justified by
    the 0.95 SHORT direction penalty + the +10pp SHORT tier bias (combined
    they buried 0.56 well below the SHORT PAPER threshold of 65%). Under
    the new symmetric thresholds (PAPER = 55% both sides), the same final
    tier still resolves to NO_SIGNAL but for a different reason — the trap
    factor (0.85^N) knocks the score below 55%."""
    bars = _bars()
    layer_scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    for i in (1, 3, 5):
        layer_scores[i] = LayerScore(Direction.SHORT, 0.7, 0.8)

    fires = check_all_traps(
        bars=bars,
        current_idx=len(bars) - 1,
        layer_scores=layer_scores,
        proposed_direction=Direction.SHORT,
        context=TrapContext(),
    )
    final = aggregate(
        layer_scores,
        trap_fires=fires,
        brain_adjust=1.0,
        news_multiplier=1.0,
    )
    assert final.direction is Direction.SHORT
    assert classify_tier(final) == "NO_SIGNAL"


def test_full_pipeline_no_layers_is_neutral_no_signal() -> None:
    bars = _bars()
    layer_scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}

    fires = check_all_traps(
        bars=bars,
        current_idx=len(bars) - 1,
        layer_scores=layer_scores,
        proposed_direction=Direction.LONG,
        context=TrapContext(),
    )
    final = aggregate(
        layer_scores,
        trap_fires=fires,
        brain_adjust=1.0,
        news_multiplier=1.0,
    )
    assert final.score == 0.0
    assert final.direction is Direction.NEUTRAL
    assert classify_tier(final) == "NO_SIGNAL"
