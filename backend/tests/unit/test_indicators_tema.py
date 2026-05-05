import math
import numpy as np
import pytest

from app.core.indicators.dema import dema
from app.core.indicators.tema import tema


def test_tema_leads_dema_on_uptrend() -> None:
    # Use a curve where DEMA hasn't fully converged so we can compare lag.
    closes = np.array([100.0 + i * 1.0 + 5.0 * np.sin(i / 3.0) for i in range(60)])
    t = tema(closes, period=10)
    d = dema(closes, period=10)
    # TEMA reduces lag further than DEMA, so on average it should be at least
    # as close to the current price as DEMA on a noisy trend.
    assert abs(t[-1] - closes[-1]) <= abs(d[-1] - closes[-1]) + 1e-9


def test_tema_leading_nan() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    t = tema(closes, period=10)
    # Needs roughly 3*(period-1) bars before producing valid output
    for i in range(3 * (10 - 1)):
        assert math.isnan(t[i])


def test_tema_no_lookahead() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    full = tema(closes, period=10)
    truncated = tema(closes[:50], period=10)
    assert full[49] == pytest.approx(truncated[49])


def test_tema_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        tema(np.array([1.0, 2.0]), period=0)
