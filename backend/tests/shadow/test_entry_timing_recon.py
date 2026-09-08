"""Entry-timing recon (2026-09-04) — log-only, flag-gated instrumentation.

Two concerns, tested separately:
  1. `_entry_timing_recon_tick`'s own arm/pair/false-start logic (this
     file, unit-level, drives the method directly).
  2. Structural proof that the whole thing is inert when
     ENTRY_TIMING_RECON_ENABLED=False (its shipped default) and touches
     nothing else in ShadowWorker's real decision path — the existing
     full shadow/breakeven/worker suite (unchanged, still green) is
     itself part of that proof; this file adds the direct unit coverage
     on top.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.shadow.worker import (
    ENTRY_TIMING_RECON_SCORE_THRESHOLD,
    ShadowWorker,
)

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def _worker() -> ShadowWorker:
    """Minimal instance — _entry_timing_recon_tick touches only
    self._entry_timing_recon_state, so session_factory/reader are never
    called and can be placeholders."""
    return ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=MagicMock(),
        reader=MagicMock(),
    )


def test_state_starts_empty() -> None:
    w = _worker()
    assert w._entry_timing_recon_state == {}


def test_real_signal_fires_immediately_no_pairing_when_nothing_armed() -> None:
    """Real trigger fires on the very first qualifying bar -- nothing to
    pair against, no state left behind."""
    w = _worker()
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW, score=0.40,
        real_signal_fired=True,
    )
    assert w._entry_timing_recon_state == {}


def test_faster_threshold_arms_when_real_signal_does_not_fire() -> None:
    w = _worker()
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW,
        score=ENTRY_TIMING_RECON_SCORE_THRESHOLD + 0.01,
        real_signal_fired=False,
    )
    assert ("BTCUSDT", "1h") in w._entry_timing_recon_state
    arm_ts, arm_score = w._entry_timing_recon_state[("BTCUSDT", "1h")]
    assert arm_ts == _NOW
    assert arm_score == pytest.approx(ENTRY_TIMING_RECON_SCORE_THRESHOLD + 0.01)


def test_below_faster_threshold_does_not_arm() -> None:
    w = _worker()
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW,
        score=ENTRY_TIMING_RECON_SCORE_THRESHOLD - 0.05,
        real_signal_fired=False,
    )
    assert w._entry_timing_recon_state == {}


def test_already_armed_does_not_rearm_on_a_later_bar() -> None:
    """Once armed, a later bar also clearing the faster threshold must
    NOT move the arm point -- we want the FIRST qualifying bar."""
    w = _worker()
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW,
        score=0.30, real_signal_fired=False,
    )
    later = _NOW + timedelta(hours=1)
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=later,
        score=0.35, real_signal_fired=False,
    )
    arm_ts, arm_score = w._entry_timing_recon_state[("BTCUSDT", "1h")]
    assert arm_ts == _NOW
    assert arm_score == pytest.approx(0.30)


def test_real_signal_after_arming_pairs_and_clears_state(caplog: pytest.LogCaptureFixture) -> None:
    w = _worker()
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW,
        score=0.30, real_signal_fired=False,
    )
    real_ts = _NOW + timedelta(hours=3)
    with caplog.at_level("INFO"):
        w._entry_timing_recon_tick(
            symbol="BTCUSDT", tf="1h", ts=real_ts,
            score=0.40, real_signal_fired=True,
        )
    assert w._entry_timing_recon_state == {}
    assert any("PAIRED" in r.message for r in caplog.records)
    assert any("bars_early=3" in r.message for r in caplog.records)


def test_false_start_clears_after_timeout_bound(caplog: pytest.LogCaptureFixture) -> None:
    """Armed candidate that never becomes a real signal, held past the
    1h timeframe's TIMEOUT_BARS_PER_TF bound, must clear itself and log
    a false start -- not stay armed forever."""
    from app.shadow.exit_monitor import TIMEOUT_BARS_PER_TF

    w = _worker()
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW,
        score=0.30, real_signal_fired=False,
    )
    limit = TIMEOUT_BARS_PER_TF["1h"]
    past_limit_ts = _NOW + timedelta(hours=limit)
    with caplog.at_level("INFO"):
        w._entry_timing_recon_tick(
            symbol="BTCUSDT", tf="1h", ts=past_limit_ts,
            score=0.10,  # below threshold -- irrelevant once past the bound
            real_signal_fired=False,
        )
    assert w._entry_timing_recon_state == {}
    assert any("FALSE_START" in r.message for r in caplog.records)


def test_state_is_keyed_per_symbol_and_timeframe_independently() -> None:
    w = _worker()
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW, score=0.30, real_signal_fired=False,
    )
    w._entry_timing_recon_tick(
        symbol="ETHUSDT", tf="1h", ts=_NOW, score=0.30, real_signal_fired=False,
    )
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="15m", ts=_NOW, score=0.30, real_signal_fired=False,
    )
    assert len(w._entry_timing_recon_state) == 3
    # A real fire for one key must not disturb the other two.
    w._entry_timing_recon_tick(
        symbol="BTCUSDT", tf="1h", ts=_NOW + timedelta(hours=1),
        score=0.40, real_signal_fired=True,
    )
    assert ("BTCUSDT", "1h") not in w._entry_timing_recon_state
    assert ("ETHUSDT", "1h") in w._entry_timing_recon_state
    assert ("BTCUSDT", "15m") in w._entry_timing_recon_state


# test_shipped_default_is_disabled was REMOVED 2026-09-08, not inverted.
#
# It asserted `ENTRY_TIMING_RECON_ENABLED is False`, which was the right
# guarantee while recon shipped disabled. That guarantee is now
# deliberately retired, and the two obvious replacements are both worse
# than nothing:
#
#   - Asserting `is True` would pass on a build where the guard had been
#     deleted entirely, since it checks a value and not the wiring.
#   - A "behavioural" version calling _entry_timing_recon_tick directly
#     cannot work either: the flag is checked at the CALL SITE in
#     _maybe_open_position, so invoking the tick bypasses the guard and
#     records state regardless of the flag. Such a test would pass
#     identically whether the flag worked or not.
#
# What still carries the guarantee is the structural test below, which
# proves the call site remains inside the flag's guard -- i.e. that the
# rollback path exists. See
# docs/superpowers/decisions/2026-09-08-checks-that-cannot-fail.md.
#
# DO NOT REINTRODUCE A FLAG-VALUE ASSERTION TO FILL THIS GAP. The check
# that the recon is actually RUNNING is its OUTPUT, not its flag: a
# constant reading True proves nothing about whether the tick fires,
# which is the same value-versus-wiring distinction that made the
# removed test worthless. The standing verification is "eligibility data
# is accumulating in the logs", and the ABSENCE of that data is the
# signal. There is no assertion about the constant that can substitute
# for looking at what the code produced.


def test_recon_call_site_is_gated_behind_the_flag_constant() -> None:
    """Source-level check that _maybe_open_position's call to
    _entry_timing_recon_tick is textually inside an
    `if ENTRY_TIMING_RECON_ENABLED:` guard, not unconditional -- the
    same class of proof used for the telegram-dedup scope tests
    (structural, not just behavioral, so a future refactor that
    accidentally removes the guard fails CI immediately rather than
    silently)."""
    from pathlib import Path

    worker_path = Path(__file__).resolve().parents[2] / "app" / "shadow" / "worker.py"
    text = worker_path.read_text(encoding="utf-8")
    guard_idx = text.index("if ENTRY_TIMING_RECON_ENABLED:")
    call_idx = text.index("self._entry_timing_recon_tick(")
    # The call must appear shortly after the guard opens, on the very
    # next non-trivial lines (inside the if-block) -- not merely
    # somewhere later in the file.
    between = text[guard_idx:call_idx]
    assert between.count("\n") < 5, (
        "the recon tick call should be the first thing inside the "
        "ENTRY_TIMING_RECON_ENABLED guard, not several statements deep"
    )


def test_bars_between_helper_is_pure_and_correct() -> None:
    w = _worker()
    n = w._entry_timing_recon_bars_between(_NOW, _NOW + timedelta(hours=5), "1h")
    assert n == 5
    n = w._entry_timing_recon_bars_between(_NOW, _NOW + timedelta(minutes=44), "1h")
    assert n == 0  # not yet a full bar
    n = w._entry_timing_recon_bars_between(_NOW, _NOW - timedelta(hours=1), "1h")
    assert n == 0  # never negative
