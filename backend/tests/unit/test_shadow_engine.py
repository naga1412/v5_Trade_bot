import math
from datetime import datetime, timezone
import pytest

from app.shadow.engine import (
    ShadowSignal,
    ShadowPosition,
    Direction,
    SignalEvaluator,
    LONG_THRESHOLD,
    SHORT_THRESHOLD,
    MIN_CONFIDENCE,
)


def test_thresholds_match_spec() -> None:
    # Spec §5.1
    assert LONG_THRESHOLD == 0.30
    assert SHORT_THRESHOLD == -0.50
    assert MIN_CONFIDENCE == 0.50


def test_shadow_signal_construction() -> None:
    sig = ShadowSignal(
        symbol="BTCUSDT", direction=Direction.LONG, score=0.65,
        confidence=0.72, entry_price=78250.0, stop_loss=76685.0,
        take_profit=81450.0, atr=781.5, layer_scores={"1": 0.85, "3": 0.72, "5": 0.40},
        ts=datetime(2026, 5, 3, 14, 23, tzinfo=timezone.utc),
    )
    assert sig.signal_id  # auto-generated short id
    assert len(sig.signal_id) >= 8
    # rr (reward/risk) for LONG = (tp - entry) / (entry - sl)
    expected_rr = (81450.0 - 78250.0) / (78250.0 - 76685.0)
    assert sig.risk_reward == pytest.approx(expected_rr, rel=1e-3)


def test_shadow_position_construction() -> None:
    sig_ts = datetime(2026, 5, 3, 14, 0, tzinfo=timezone.utc)
    sig = ShadowSignal(
        symbol="ETHUSDT", direction=Direction.SHORT, score=-0.61,
        confidence=0.68, entry_price=3950.0, stop_loss=4019.1,
        take_profit=3812.5, atr=46.1, layer_scores={"1": -0.7, "3": -0.5},
        ts=sig_ts,
    )
    pos = ShadowPosition.from_signal(sig, position_size_usdt=30.0)
    assert pos.symbol == "ETHUSDT"
    assert pos.direction is Direction.SHORT
    assert pos.entry_price == 3950.0
    assert pos.position_size_usdt == 30.0
    assert pos.bars_held == 0
    assert pos.opened_at == sig_ts
