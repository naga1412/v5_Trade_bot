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


def make_evaluator() -> SignalEvaluator:
    return SignalEvaluator()


def test_evaluator_long_above_threshold_with_high_confidence() -> None:
    ev = make_evaluator()
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.45, confidence=0.72,
        last_close=78250.0, atr=781.5,
        layer_scores={"1": 0.6, "3": 0.5, "5": 0.4},
        ts=datetime(2026, 5, 3, tzinfo=timezone.utc),
    )
    assert sig is not None
    assert sig.direction is Direction.LONG
    assert sig.entry_price == 78250.0
    # SL = 78250 - 1.5 * 781.5 = 77077.75
    assert sig.stop_loss == pytest.approx(77077.75)
    # TP = 78250 + 3.0 * 781.5 = 80594.5
    assert sig.take_profit == pytest.approx(80594.5)


def test_evaluator_short_below_threshold_with_high_confidence() -> None:
    ev = make_evaluator()
    sig = ev.evaluate(
        symbol="ETHUSDT", score=-0.61, confidence=0.68,
        last_close=3950.0, atr=46.1,
        layer_scores={"1": -0.7, "3": -0.5},
        ts=datetime(2026, 5, 3, tzinfo=timezone.utc),
    )
    assert sig is not None
    assert sig.direction is Direction.SHORT
    # SL = 3950 + 1.5 * 46.1 = 4019.15
    assert sig.stop_loss == pytest.approx(4019.15)
    # TP = 3950 - 3.0 * 46.1 = 3811.7
    assert sig.take_profit == pytest.approx(3811.7)


def test_evaluator_returns_none_when_below_long_threshold() -> None:
    ev = make_evaluator()
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.20, confidence=0.80,
        last_close=78000.0, atr=500.0, layer_scores={}, ts=datetime.now(timezone.utc),
    )
    assert sig is None


def test_evaluator_returns_none_when_above_short_threshold() -> None:
    ev = make_evaluator()
    sig = ev.evaluate(
        symbol="BTCUSDT", score=-0.30, confidence=0.80,
        last_close=78000.0, atr=500.0, layer_scores={}, ts=datetime.now(timezone.utc),
    )
    assert sig is None


def test_evaluator_returns_none_when_confidence_too_low() -> None:
    ev = make_evaluator()
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.80, confidence=0.40,  # high score but low confidence
        last_close=78000.0, atr=500.0, layer_scores={}, ts=datetime.now(timezone.utc),
    )
    assert sig is None


def test_evaluator_returns_none_when_atr_zero() -> None:
    ev = make_evaluator()
    sig = ev.evaluate(
        symbol="BTCUSDT", score=0.80, confidence=0.80,
        last_close=78000.0, atr=0.0, layer_scores={}, ts=datetime.now(timezone.utc),
    )
    assert sig is None


from datetime import timedelta

from app.shadow.engine import PositionGate


def test_position_gate_blocks_when_already_open() -> None:
    gate = PositionGate(open_symbols=set(["BTCUSDT"]), cooldowns={})
    assert gate.is_blocked("BTCUSDT", now=datetime.now(timezone.utc)) is True
    assert gate.is_blocked("ETHUSDT", now=datetime.now(timezone.utc)) is False


def test_position_gate_blocks_during_cooldown() -> None:
    now = datetime(2026, 5, 3, 14, 0, tzinfo=timezone.utc)
    gate = PositionGate(
        open_symbols=set(),
        cooldowns={"BTCUSDT": now + timedelta(minutes=15)},
    )
    assert gate.is_blocked("BTCUSDT", now=now) is True
    assert gate.is_blocked("BTCUSDT", now=now + timedelta(minutes=20)) is False


def test_position_gate_allows_when_clear() -> None:
    gate = PositionGate(open_symbols=set(), cooldowns={})
    assert gate.is_blocked("BTCUSDT", now=datetime.now(timezone.utc)) is False
