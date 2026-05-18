"""PR9 split_entries — multi-entry tranche split (Phase 4).

Pure function: no DB / no I/O. Tests cover the threshold gate, ratio
math, rounding invariant, and configuration validation.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.trading.dynamic_sizing import split_entries


def _settings(
    threshold: float = 0.75,
    ratios: list[float] | None = None,
):
    return SimpleNamespace(
        SIZING_MULTI_ENTRY_THRESHOLD=threshold,
        SIZING_MULTI_ENTRY_RATIOS=ratios or [0.6, 0.4],
    )


def test_no_split_when_confidence_at_threshold() -> None:
    """confidence == threshold → single tranche (boundary)."""
    result = split_entries(100.0, 75.0, _settings())
    assert result == [100.0]


def test_no_split_when_confidence_above_threshold() -> None:
    result = split_entries(100.0, 80.0, _settings())
    assert result == [100.0]


def test_split_when_confidence_below_threshold() -> None:
    """confidence < threshold → 2 tranches at [60.0, 40.0]."""
    result = split_entries(100.0, 70.0, _settings())
    assert len(result) == 2
    assert result[0] == 60.0
    assert result[1] == 40.0


def test_split_sum_equals_total_exactly() -> None:
    """Rounding loss accumulates in last tranche; sum is exact."""
    # 100.0 * 0.6 = 60.0 (exact); 100.0 * 0.4 = 40.0 (exact)
    result = split_entries(100.0, 50.0, _settings())
    assert abs(sum(result) - 100.0) < 1e-9


def test_split_sum_exact_with_irrational_ratios() -> None:
    """Bad-rounding case: 1/3 + 2/3 = 1.0 but binary float doesn't add."""
    # Try a total that doesn't divide cleanly
    result = split_entries(99.99, 50.0, _settings())
    assert abs(sum(result) - 99.99) < 1e-9
    # Last tranche absorbs the rounding error


def test_split_3_tranches_supported() -> None:
    """Operator can configure 3 tranches via SIZING_MULTI_ENTRY_RATIOS."""
    settings = _settings(ratios=[0.5, 0.3, 0.2])
    result = split_entries(100.0, 50.0, settings)
    assert len(result) == 3
    assert abs(result[0] - 50.0) < 1e-9
    assert abs(result[1] - 30.0) < 1e-9
    assert abs(result[2] - 20.0) < 1e-9


def test_split_raises_on_ratios_not_summing_to_one() -> None:
    """Invariant: ratios MUST sum to 1.0. Bad config → ValueError."""
    settings = _settings(ratios=[0.6, 0.3])  # sums to 0.9
    with pytest.raises(ValueError, match="must sum to 1.0"):
        split_entries(100.0, 50.0, settings)


def test_split_raises_on_negative_total() -> None:
    """Negative total margin is invalid input."""
    with pytest.raises(ValueError, match=">= 0"):
        split_entries(-100.0, 50.0, _settings())


def test_split_zero_total_returns_zero_tranches() -> None:
    """Zero margin (no-edge Kelly) → [0.0] not split (above threshold check)."""
    result = split_entries(0.0, 80.0, _settings())
    assert result == [0.0]


def test_split_zero_total_below_threshold_still_splits_to_zeros() -> None:
    """Zero margin below threshold: 2 tranches of 0.0 each."""
    result = split_entries(0.0, 50.0, _settings())
    assert result == [0.0, 0.0]


def test_operator_threshold_override() -> None:
    """Lower threshold to 0.5 — more signals get single-entry treatment."""
    settings = _settings(threshold=0.5)
    # confidence 60 (= 0.6) >= 0.5 → no split
    assert split_entries(100.0, 60.0, settings) == [100.0]
    # confidence 40 (= 0.4) < 0.5 → split
    assert len(split_entries(100.0, 40.0, settings)) == 2
