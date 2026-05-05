import pandas as pd

from app.core.scoring.layer5_volume import score
from app.core.scoring.types import Direction


def make_bars(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts"] = pd.date_range("2026-01-01", periods=len(rows), freq="1h", tz="UTC")
    return df.set_index("ts")


def base_rows(n: int, close: float = 100.0, vol: float = 1000.0) -> list[dict]:
    return [{"open": close, "high": close + 1, "low": close - 1,
             "close": close, "volume": vol} for _ in range(n)]


def test_volume_spike_with_bullish_bar_gives_long() -> None:
    rows = base_rows(20)
    rows.append({"open": 100, "high": 105, "low": 99, "close": 104, "volume": 5000})
    result = score(make_bars(rows))
    assert result is not None
    assert result.direction is Direction.LONG
    assert result.strength > 0.5


def test_volume_spike_with_bearish_bar_gives_short() -> None:
    rows = base_rows(20)
    rows.append({"open": 100, "high": 101, "low": 95, "close": 96, "volume": 5000})
    result = score(make_bars(rows))
    assert result is not None
    assert result.direction is Direction.SHORT


def test_low_volume_bar_gives_neutral() -> None:
    rows = base_rows(20)
    rows.append({"open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 200})
    result = score(make_bars(rows))
    assert result is not None
    assert result.direction is Direction.NEUTRAL


def test_insufficient_data_returns_none() -> None:
    rows = base_rows(10)
    assert score(make_bars(rows)) is None
