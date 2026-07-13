"""PR3 settings defaults — spec §4.1 + §4.6b + §6.1 hard bounds.

PR3 adds 5 fields to control the 15m lane + narrow universe + G1
scaling. All field defaults reproduce PR2 behavior EXCEPT
`SHADOW_TIMEFRAMES` which is the one explicit recording behavior flip
(["1h"] → ["1h", "15m"]). Live trading paths are NOT affected
(`SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False`, `HOLD_TP_SCALING_ENABLED=False`).
"""
from __future__ import annotations

from app.config import Settings


def _s(**kwargs) -> Settings:
    return Settings(
        database_url="postgresql://x", redis_url="redis://x", **kwargs,
    )


def test_shadow_timeframes_default_is_1h_and_15m() -> None:
    """The one explicit behavior flip from PR2: shadow now runs 2 TFs."""
    assert _s().SHADOW_TIMEFRAMES == ["1h", "15m"]


def test_shadow_prewarm_bars_default_200() -> None:
    """Matches the MTF cache cap; the worker reuses _KLINE_CACHE when warm."""
    assert _s().SHADOW_PREWARM_BARS == 200


def test_shadow_cooldown_hours_default_dict() -> None:
    """Both TFs default to 4h — prevents re-entry on the same choppy signal."""
    s = _s()
    assert s.SHADOW_COOLDOWN_HOURS == {"1h": 4.0, "15m": 4.0}


def test_shadow_narrow_universe_default_empty() -> None:
    """Empty list = use full top-30 universe. PR1 behavior preserved."""
    assert _s().SHADOW_NARROW_UNIVERSE == []


def test_shadow_15m_eligible_for_promotion_default_false() -> None:
    """Spec §6.1 hard bound: 15m is recording-only by default."""
    assert _s().SHADOW_15M_ELIGIBLE_FOR_PROMOTION is False


def test_env_var_override_shadow_timeframes_rollback(monkeypatch) -> None:
    """Spec §8 rollback path: SHADOW_TIMEFRAMES=["1h"] disables 15m lane."""
    monkeypatch.setenv("SHADOW_TIMEFRAMES", '["1h"]')
    assert _s().SHADOW_TIMEFRAMES == ["1h"]


def test_env_var_override_narrow_universe(monkeypatch) -> None:
    """Pydantic v2 BaseSettings parses JSON-encoded lists from env vars."""
    monkeypatch.setenv("SHADOW_NARROW_UNIVERSE", '["BTCUSDT", "ETHUSDT"]')
    assert _s().SHADOW_NARROW_UNIVERSE == ["BTCUSDT", "ETHUSDT"]


def test_env_var_override_shadow_15m_eligible(monkeypatch) -> None:
    """When the operator decides 15m win-rate justifies promotion."""
    monkeypatch.setenv("SHADOW_15M_ELIGIBLE_FOR_PROMOTION", "true")
    assert _s().SHADOW_15M_ELIGIBLE_FOR_PROMOTION is True


# --- G1 (Phase 5.5.2) ------------------------------------------------------


def test_hold_tp_scaling_default_off() -> None:
    """Spec §6.1 hard bound: G1 ships default-OFF; flipping enables the
    table lookup in shadow_worker open-trade hook."""
    assert _s().HOLD_TP_SCALING_ENABLED is False


def test_hold_tp_scaling_table_default() -> None:
    """The fixed table from spec §4.6b:
        mtf_agreement 3 → 24 bars / 1.0× (baseline for 1h)
        mtf_agreement 4 → 48 bars / 1.25×
        mtf_agreement 5 → 96 bars / 1.5×
        mtf_agreement 6 → 168 bars / 2.0×
    """
    s = _s()
    assert s.HOLD_TP_SCALING_TABLE == {
        3: (24, 1.0),
        4: (48, 1.25),
        5: (96, 1.5),
        6: (168, 2.0),
    }


def test_hold_tp_scaling_env_var_override(monkeypatch) -> None:
    """Operator flips HOLD_TP_SCALING_ENABLED=true per-env after staging
    validates scaled trades behave (per-TF win-rate + hold-time dist)."""
    monkeypatch.setenv("HOLD_TP_SCALING_ENABLED", "true")
    assert _s().HOLD_TP_SCALING_ENABLED is True
