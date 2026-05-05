from datetime import datetime, timezone

from app.core.dataquality.validator import (
    Candle, validate
)


def make_candle(**overrides) -> Candle:
    base = dict(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        open=100.0, high=110.0, low=95.0, close=105.0, volume=1000.0,
    )
    base.update(overrides)
    return Candle(**base)


def test_valid_candle_passes() -> None:
    result = validate(make_candle(), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is True
    assert result.failures == ()


def test_high_below_low_fails() -> None:
    result = validate(make_candle(high=50.0, low=100.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "high_below_low" in result.failures


def test_open_outside_range_fails() -> None:
    result = validate(make_candle(open=200.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "open_outside_range" in result.failures


def test_close_outside_range_fails() -> None:
    result = validate(make_candle(close=200.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "close_outside_range" in result.failures


def test_negative_volume_fails() -> None:
    result = validate(make_candle(volume=-1.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "negative_volume" in result.failures


def test_price_jump_over_20pct_fails() -> None:
    result = validate(make_candle(close=130.0, high=130.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "price_jump_over_20pct" in result.failures


def test_volume_spike_over_10x_median_fails() -> None:
    result = validate(make_candle(volume=20000.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "volume_spike_over_10x_median" in result.failures
