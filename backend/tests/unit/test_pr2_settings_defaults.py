"""PR2 Settings defaults — codify the design's §6.1 bound that every
new flag reproduces PR1 behavior EXCEPT MTF_MIN_AGREEMENT_1H=3 (the
one explicit behavior flip)."""
from __future__ import annotations

from app.config import Settings


def test_mtf_gate_defaults() -> None:
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.MTF_MIN_AGREEMENT_1H == 3
    assert s.MTF_HIGHER_TF_VETO is True


def test_short_safety_flags_default_off() -> None:
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.SHORT_FUNDING_HALVE_HOLD is False
    assert s.SHORT_TIGHTEN_SL_LOW_MTF is False
    assert s.SHORT_VETO_HIGH_BORROW is False


def test_short_threshold_knobs() -> None:
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.SHORT_FUNDING_HALVE_THRESHOLD_PCT == 0.05
    assert s.SHORT_VETO_BORROW_APR_PCT == 10.0
    assert s.SHORT_TIGHTEN_SL_MTF_CUTOFF == 5
    assert s.SHORT_TIGHTEN_SL_PCT == 0.20


def test_env_var_override_persists(monkeypatch) -> None:
    monkeypatch.setenv("MTF_MIN_AGREEMENT_1H", "5")
    monkeypatch.setenv("SHORT_VETO_HIGH_BORROW", "true")
    s = Settings(database_url="postgresql://x", redis_url="redis://x")
    assert s.MTF_MIN_AGREEMENT_1H == 5
    assert s.SHORT_VETO_HIGH_BORROW is True


def test_mtf_min_agreement_zero_is_valid_rollback() -> None:
    """Per spec §6.1: MTF_MIN_AGREEMENT_1H=0 is the single-env-var
    rollback path. 0 must validate cleanly (not fail at < 0 check)."""
    s = Settings(
        database_url="postgresql://x",
        redis_url="redis://x",
        MTF_MIN_AGREEMENT_1H=0,
    )
    assert s.MTF_MIN_AGREEMENT_1H == 0
