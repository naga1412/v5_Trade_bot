"""PR8 cooldown_compute — pure-function tests.

Exercises the duration table + the is_cooldown_blocked decision matrix
without any DB or session. Uses SimpleNamespace for the settings + row
protocols.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.trading.cooldown_compute import (
    compute_cooldown_duration,
    is_cooldown_blocked,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _settings(
    *,
    enabled: bool = True,
    sl_fresh_mtf: bool = True,
    table: dict[str, float] | None = None,
):
    return SimpleNamespace(
        LIVE_COOLDOWN_ENABLED=enabled,
        LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=sl_fresh_mtf,
        LIVE_COOLDOWN_HOURS_BY_OUTCOME=table or {
            "stop_loss": 8.0,
            "take_profit": 1.0,
            "timeout": 4.0,
            "manual_close": 0.0,
            "external_close": 0.0,
            "liquidation_buffer_breach": 24.0,
        },
    )


# --- compute_cooldown_duration ------------------------------------------


def test_compute_cooldown_duration_stop_loss() -> None:
    assert compute_cooldown_duration("stop_loss", _settings()) == timedelta(hours=8)


def test_compute_cooldown_duration_take_profit() -> None:
    assert compute_cooldown_duration("take_profit", _settings()) == timedelta(hours=1)


def test_compute_cooldown_duration_timeout() -> None:
    assert compute_cooldown_duration("timeout", _settings()) == timedelta(hours=4)


def test_compute_cooldown_duration_liquidation_buffer_breach() -> None:
    assert compute_cooldown_duration(
        "liquidation_buffer_breach", _settings(),
    ) == timedelta(hours=24)


def test_compute_cooldown_duration_zero_for_manual_and_external() -> None:
    assert compute_cooldown_duration("manual_close", _settings()) == timedelta(0)
    assert compute_cooldown_duration("external_close", _settings()) == timedelta(0)


def test_compute_cooldown_duration_unknown_falls_back_to_timeout_baseline() -> None:
    """Defensive: an outcome string not in the dict gets the 4h fallback.

    This codepath only fires if someone bypasses LiveExitReason. A
    settings-defaults test asserts every enum value IS in the dict, so
    this is a belt-and-braces guard.
    """
    assert compute_cooldown_duration(
        "future_outcome_we_havent_named_yet", _settings(),
    ) == timedelta(hours=4)


# --- is_cooldown_blocked matrix -----------------------------------------


def test_blocked_when_gate_disabled_returns_false() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss", last_mtf_agreement=3,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=5, settings=_settings(enabled=False),
    )
    assert not blocked
    assert reason == "cooldown_disabled"


def test_blocked_when_no_row_returns_false() -> None:
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=None,
        new_mtf_agreement=5, settings=_settings(),
    )
    assert not blocked
    assert reason == "no_cooldown"


def test_blocked_when_calendar_active() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW + timedelta(hours=2),
        last_exit_reason="stop_loss", last_mtf_agreement=3,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=5, settings=_settings(),
    )
    assert blocked
    assert reason.startswith("calendar_until_")


def test_blocked_after_sl_with_stale_mtf() -> None:
    """Calendar expired but SL + stale-or-equal MTF still blocks."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=4, settings=_settings(),  # SAME mtf — still stale
    )
    assert blocked
    assert "sl_stale_mtf" in reason


def test_blocked_after_sl_with_lower_mtf() -> None:
    """Strictly-lower MTF after SL is also stale."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, _ = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=3, settings=_settings(),
    )
    assert blocked


def test_cleared_after_sl_with_fresh_mtf() -> None:
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, reason = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=5, settings=_settings(),  # HIGHER mtf
    )
    assert not blocked
    assert reason == "cleared"


def test_cleared_after_tp_calendar_expired() -> None:
    """TP doesn't require fresh MTF — calendar alone gates."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(minutes=1),
        last_exit_reason="take_profit", last_mtf_agreement=4,
    )
    blocked, _ = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=3, settings=_settings(),
    )
    assert not blocked


def test_sl_fresh_mtf_disabled_via_flag() -> None:
    """LIVE_COOLDOWN_SL_REQUIRES_FRESH_MTF=False — calendar alone gates."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, _ = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=4, settings=_settings(sl_fresh_mtf=False),
    )
    assert not blocked


def test_sl_fresh_mtf_handles_null_last_mtf() -> None:
    """Historic row with last_mtf_agreement=None: treat as 0 → any new_mtf>0 clears."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=None,
    )
    blocked, _ = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=1, settings=_settings(),
    )
    assert not blocked


def test_sl_fresh_mtf_handles_null_new_mtf() -> None:
    """New signal with mtf=None: treat as 0 → blocked unless last was also 0/null."""
    row = SimpleNamespace(
        cooldown_until=_NOW - timedelta(hours=1),
        last_exit_reason="stop_loss", last_mtf_agreement=4,
    )
    blocked, _ = is_cooldown_blocked(
        now=_NOW, cooldown_row=row,
        new_mtf_agreement=None, settings=_settings(),
    )
    assert blocked
