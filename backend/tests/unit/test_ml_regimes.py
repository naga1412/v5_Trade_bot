"""Unit tests for app/ml/regimes.py — 5 frozen regime windows + acceptance gate (SP-1 B1)."""
from datetime import datetime, timezone

from app.ml.regimes import REGIME_WINDOWS, RegimeWindow


def test_five_named_regime_windows_present() -> None:
    names = {w.name for w in REGIME_WINDOWS}
    assert names == {
        "bull_breakout",
        "bear_crash",
        "sideways_grind",
        "high_volatility",
        "low_volatility",
    }


def test_each_window_has_valid_date_range() -> None:
    for w in REGIME_WINDOWS:
        assert isinstance(w, RegimeWindow)
        assert isinstance(w.start, datetime)
        assert isinstance(w.end, datetime)
        assert w.start.tzinfo is timezone.utc
        assert w.end.tzinfo is timezone.utc
        assert w.start < w.end
        assert (w.end - w.start).days >= 14  # all windows >= 2 weeks


def test_acceptance_threshold_constant() -> None:
    from app.ml.regimes import ACCEPTANCE_MAE_THRESHOLD

    assert ACCEPTANCE_MAE_THRESHOLD == 0.015  # spec sec 2 row 13: 1.5%
