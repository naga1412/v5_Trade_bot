from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    failures: tuple[str, ...] = field(default_factory=tuple)


_PRICE_JUMP_LIMIT = 0.20
_VOLUME_SPIKE_LIMIT = 10.0


def validate(
    candle: Candle, *, prev_close: float | None, prev_volume_median: float | None
) -> ValidationResult:
    failures: list[str] = []

    if candle.high < candle.low:
        failures.append("high_below_low")
    if not (candle.low <= candle.open <= candle.high):
        failures.append("open_outside_range")
    if not (candle.low <= candle.close <= candle.high):
        failures.append("close_outside_range")
    if candle.volume < 0:
        failures.append("negative_volume")

    if prev_close is not None and prev_close > 0:
        jump = abs(candle.close - prev_close) / prev_close
        if jump > _PRICE_JUMP_LIMIT:
            failures.append("price_jump_over_20pct")

    if prev_volume_median is not None and prev_volume_median > 0:
        if candle.volume > _VOLUME_SPIKE_LIMIT * prev_volume_median:
            failures.append("volume_spike_over_10x_median")

    return ValidationResult(ok=not failures, failures=tuple(failures))
