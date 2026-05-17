"""PR3: ShadowPosition gains 3 new fields (timeframe + 2 G1 scaling).

Per the PR3 plan Phase 2.2 + 5.5.4:
  - `timeframe: str = "1h"` (PR1/PR2 callers stay bit-identical)
  - `hold_scaling_factor: float | None = None` (G1 — populated when scaling ON)
  - `hold_timeout_bars: int | None = None` (G1 — populated when scaling ON)

All three default to safe baselines so the pre-PR3 call sites that
don't pass them continue to construct positions exactly as before.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.shadow.engine import Direction, ShadowPosition


def _make_position(**overrides) -> ShadowPosition:
    """Minimal kwargs satisfying the existing required fields."""
    base = dict(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        position_size_usdt=10.0,
        entry_score=0.4,
        entry_confidence=0.6,
        entry_atr=1.5,
        layer_scores={},
        bars_held=0,
        opened_at=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        last_check_at=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        signal_id="sig_abc",
    )
    base.update(overrides)
    return ShadowPosition(**base)


# --- Phase 2.2: timeframe field --------------------------------------------


def test_position_timeframe_default_is_1h() -> None:
    """Pre-PR3 callers (omit timeframe kwarg) get '1h' — bit-identical."""
    p = _make_position()
    assert p.timeframe == "1h"


def test_position_timeframe_explicit_15m() -> None:
    """PR3 caller (worker, post-Phase 4) passes per-stream TF."""
    p = _make_position(timeframe="15m")
    assert p.timeframe == "15m"


# --- Phase 5.5.4: G1 scaling fields ----------------------------------------


def test_position_hold_scaling_default_none() -> None:
    """Pre-G1 callers + flag-OFF callers get NULL on both columns.
    Recording contract: NULL means 'scaling was not applied to this trade'."""
    p = _make_position()
    assert p.hold_scaling_factor is None
    assert p.hold_timeout_bars is None


def test_position_hold_scaling_explicit_values() -> None:
    """G1 flag ON + mtf_agreement=5 + 1h → (1.5, 96)."""
    p = _make_position(
        hold_scaling_factor=1.5,
        hold_timeout_bars=96,
    )
    assert p.hold_scaling_factor == 1.5
    assert p.hold_timeout_bars == 96


def test_position_hold_scaling_zero_factor_is_distinct_from_none() -> None:
    """Edge case: factor 0.0 is a meaningful value (would zero out the TP
    distance). NULL means 'not applied'; 0.0 means 'applied with zero
    multiplier'. The recording contract distinguishes."""
    p = _make_position(hold_scaling_factor=0.0, hold_timeout_bars=0)
    assert p.hold_scaling_factor == 0.0
    assert p.hold_scaling_factor is not None  # Explicitly not-NULL


def test_position_all_pr3_fields_can_coexist() -> None:
    """timeframe + G1 fields populate together on a 15m + scaled position."""
    p = _make_position(
        timeframe="15m",
        hold_scaling_factor=1.25,
        hold_timeout_bars=192,  # 96 (15m baseline) × (48/24) per spec §4.6b
    )
    assert p.timeframe == "15m"
    assert p.hold_scaling_factor == 1.25
    assert p.hold_timeout_bars == 192
