"""Tests for funding_directional — PR1 helper.

Covers:
  - Positive funding boosts SHORT; negative funding boosts LONG.
  - Deadband (|rate| <= FUNDING_DEADBAND) returns 0.0.
  - Exact boundary ±0.0005 returns 0.0 (strict gt, not gte).
  - Just outside boundary ±0.000501 returns +0.10.
  - None funding_rate returns None.
  - NEUTRAL direction always returns None regardless of funding.
  - Wrong-side: positive funding does NOT boost LONG; negative does NOT boost SHORT.
"""
from __future__ import annotations

import pytest

from app.core.scoring.funding_directional import (
    BOOST,
    FUNDING_DEADBAND,
    compute_funding_directional_adj,
)
from app.core.scoring.types import Direction


def test_positive_funding_boosts_short() -> None:
    """funding_rate > deadband + LONG-side pays → SHORT should receive boost."""
    result = compute_funding_directional_adj(0.0008, Direction.SHORT)
    assert result == pytest.approx(BOOST)


def test_negative_funding_boosts_long() -> None:
    """funding_rate < -deadband + SHORT-side pays → LONG should receive boost."""
    result = compute_funding_directional_adj(-0.0008, Direction.LONG)
    assert result == pytest.approx(BOOST)


def test_deadband_returns_zero_for_long() -> None:
    """Funding within deadband for LONG returns 0.0."""
    result = compute_funding_directional_adj(0.0002, Direction.LONG)
    assert result == 0.0


def test_deadband_returns_zero_for_short() -> None:
    """Funding within deadband for SHORT returns 0.0."""
    result = compute_funding_directional_adj(-0.0002, Direction.SHORT)
    assert result == 0.0


def test_zero_funding_returns_zero() -> None:
    """funding_rate=0.0 is inside the deadband → returns 0.0."""
    assert compute_funding_directional_adj(0.0, Direction.LONG) == 0.0
    assert compute_funding_directional_adj(0.0, Direction.SHORT) == 0.0


def test_boundary_exactly_at_deadband_returns_zero() -> None:
    """Exact ±FUNDING_DEADBAND must return 0.0 (strict gt, not gte)."""
    # Positive side
    assert compute_funding_directional_adj(FUNDING_DEADBAND, Direction.SHORT) == 0.0
    # Negative side
    assert compute_funding_directional_adj(-FUNDING_DEADBAND, Direction.LONG) == 0.0


def test_just_outside_deadband_returns_boost() -> None:
    """±(FUNDING_DEADBAND + epsilon) must return +BOOST."""
    epsilon = 0.000001
    # Positive just outside → boost SHORT
    result_short = compute_funding_directional_adj(
        FUNDING_DEADBAND + epsilon, Direction.SHORT
    )
    assert result_short == pytest.approx(BOOST)
    # Negative just outside → boost LONG
    result_long = compute_funding_directional_adj(
        -(FUNDING_DEADBAND + epsilon), Direction.LONG
    )
    assert result_long == pytest.approx(BOOST)


def test_funding_rate_none_returns_none() -> None:
    """None funding_rate always returns None regardless of direction."""
    assert compute_funding_directional_adj(None, Direction.LONG) is None
    assert compute_funding_directional_adj(None, Direction.SHORT) is None
    assert compute_funding_directional_adj(None, Direction.NEUTRAL) is None


def test_neutral_direction_returns_none_regardless_of_funding() -> None:
    """NEUTRAL direction always returns None even when funding is large."""
    assert compute_funding_directional_adj(0.001, Direction.NEUTRAL) is None
    assert compute_funding_directional_adj(-0.001, Direction.NEUTRAL) is None
    assert compute_funding_directional_adj(0.0, Direction.NEUTRAL) is None


def test_long_with_high_positive_funding_returns_zero() -> None:
    """Positive funding is unfavorable for LONG (long-side pays) → no boost."""
    result = compute_funding_directional_adj(0.001, Direction.LONG)
    assert result == 0.0


def test_short_with_high_negative_funding_returns_zero() -> None:
    """Negative funding is unfavorable for SHORT (short-side pays) → no boost."""
    result = compute_funding_directional_adj(-0.001, Direction.SHORT)
    assert result == 0.0
