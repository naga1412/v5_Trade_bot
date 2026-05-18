"""PR8 settings defaults — all default-OFF for prod safety.

Sanity-checks that the 4 new PR8 settings + LiveExitReason enum landed
with the values the spec mandates. Operator can override via env vars
per the standard BaseSettings flow.
"""
from __future__ import annotations

from app.config import get_settings
from app.db.live_exit_reasons import LiveExitReason


def test_live_cooldown_enabled_default_false() -> None:
    """Default-OFF in prod — operator flips per env after soak."""
    assert get_settings().LIVE_COOLDOWN_ENABLED is False


def test_live_cooldown_hours_by_outcome_defaults() -> None:
    """Per-outcome durations per spec §5."""
    table = get_settings().LIVE_COOLDOWN_HOURS_BY_OUTCOME
    assert table["stop_loss"] == 8.0
    assert table["take_profit"] == 1.0
    assert table["timeout"] == 4.0
    assert table["manual_close"] == 0.0
    assert table["external_close"] == 0.0
    assert table["liquidation_buffer_breach"] == 24.0


def test_live_cooldown_hours_covers_every_exit_reason() -> None:
    """Every enum value MUST have a duration — keeps compute_cooldown_duration
    from silently falling back for new outcomes."""
    table = get_settings().LIVE_COOLDOWN_HOURS_BY_OUTCOME
    for reason in LiveExitReason:
        assert reason.value in table, f"missing duration for {reason.value}"


def test_live_cooldown_sl_requires_fresh_mtf_default_true() -> None:
    """Spec §5: SL → require fresh MTF on the new signal to clear cooldown."""
    assert get_settings().LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF is True


def test_live_cooldown_regime_aware_default_false() -> None:
    """Forward-compat flag — no detector exists yet."""
    assert get_settings().LIVE_COOLDOWN_REGIME_AWARE is False


def test_live_exit_reason_enum_values() -> None:
    """6 exit reasons, all string-valued."""
    values = {r.value for r in LiveExitReason}
    assert values == {
        "take_profit", "stop_loss", "timeout",
        "manual_close", "external_close", "liquidation_buffer_breach",
    }
