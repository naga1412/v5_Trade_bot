"""Trap detection primitives — spec §3.3.

A `TrapFire` is the value object every trap returns when it fires. A `Trap`
is a Protocol describing the detector interface — mirrors `Pattern`/`PatternFire`
in `app/core/patterns/base.py`. `TrapContext` carries cross-cutting inputs
(news calendar, weekly bias, BTC volatility, funding rate) that traps share.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import pandas as pd

from app.core.scoring.types import Direction, LayerScore

log = logging.getLogger(__name__)

Severity = Literal["medium", "high", "extreme"]
Side = Literal["long", "short", "both"]

_VALID_SEVERITY: frozenset[str] = frozenset({"medium", "high", "extreme"})
_VALID_SIDE: frozenset[str] = frozenset({"long", "short", "both"})


@dataclass(frozen=True)
class TrapFire:
    """A single trap firing at one bar.

    Attributes:
        trap_id: stable snake_case id used as the lookup key in `trap_enabled`.
        severity: one of {medium, high, extreme}; informational, not a multiplier.
        side: {long, short, both} — which proposed direction this trap warns against.
        reason: short human-readable explanation (lands in JSONB, capped at 200 chars).
        evidence: free-form dict for diagnostics (e.g. swept-level price, funding-rate value).
    """
    trap_id: str
    severity: Severity
    side: Side
    reason: str
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITY:
            raise ValueError(
                f"severity must be one of {sorted(_VALID_SEVERITY)}, got {self.severity!r}"
            )
        if self.side not in _VALID_SIDE:
            raise ValueError(
                f"side must be one of {sorted(_VALID_SIDE)}, got {self.side!r}"
            )


@dataclass(frozen=True)
class TrapContext:
    """Cross-cutting context shared across all traps.

    Per spec §10 Q4, fields without a live data source default to `None` so
    the trap that needs them gracefully skips. SP-5 ships with most fields
    `None`; SP-3.5 (adapter additions) and SP-9 (news ingest) wire them in.
    """
    next_news_event_minutes_until: int | None = None
    is_friday_close: bool = False
    weekly_bias: Direction = Direction.NEUTRAL
    btc_atr_pct: float | None = None
    funding_rate: float | None = None
    open_interest_delta_24h: float | None = None
    borrow_rate_pct: float | None = None
    # Identity (used by symbol-aware traps like alt_btc_indecision)
    symbol: str = ""
    timeframe: str = ""


@runtime_checkable
class Trap(Protocol):
    """Detector protocol — every trap implements this."""
    trap_id: str
    severity: Severity
    side: Side

    def check(
        self,
        bars: pd.DataFrame,
        *,
        current_idx: int,
        layer_scores: dict[int, LayerScore | None],
        proposed_direction: Direction,
        context: TrapContext,
    ) -> TrapFire | None:
        """Return `TrapFire` if the trap fires AGAINST `proposed_direction`.

        Implementations MUST NOT raise on bad input — return `None` instead.
        The orchestrator wraps each call in try/except to defend against a
        single broken detector bricking the whole trap stack.
        """
        ...


# 2026-08-14 remediation work order B2: run_traps.py's orchestrator wrapped
# `trap.check(...)` in a bare `except Exception: continue` with zero
# logging — same failure class as the flow_features endpoint-swallow fixed
# in PR #423, and the same shape layer2_patterns.py / layer6_micro.py had
# (see app/core/patterns/base.py's detect_safe, the sibling fix). A single
# trap raising on one bar is an acceptable defensive catch; the SAME trap
# raising on every consecutive call is a structurally broken detector —
# exactly how any of the 17 registered traps could silently never fire
# without anyone noticing. Tracks failures per `trap_id`, escalates once
# a specific trap looks systematic.
_CONSECUTIVE_FAILURE_ALERT_THRESHOLD: int = 20
_consecutive_failures: dict[str, int] = {}


def _record_trap_result(trap_id: str, *, ok: bool) -> None:
    if ok:
        _consecutive_failures[trap_id] = 0
        return
    streak = _consecutive_failures.get(trap_id, 0) + 1
    _consecutive_failures[trap_id] = streak
    if streak >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
        log.error(
            "trap %r has raised on %d consecutive check() calls — this "
            "looks like a structurally broken detector, not a one-off "
            "bad-bar edge case (Trap.check's contract is to return None "
            "on bad input, not raise). Same failure class as the "
            "flow_features endpoint-swallow fixed in PR #423.",
            trap_id, streak,
        )


def _clear_trap_failure_streaks_for_tests() -> None:
    _consecutive_failures.clear()


def check_safe(
    trap: "Trap",
    bars: pd.DataFrame,
    *,
    current_idx: int,
    layer_scores: dict[int, LayerScore | None],
    proposed_direction: Direction,
    context: TrapContext,
) -> "TrapFire | None":
    """Call `trap.check()`, tracking per-trap-id consecutive failures.

    A single broken trap must not brick the whole stack (unchanged
    contract — this still returns None on any exception) but silently
    swallowing forever is how a genuinely broken detector goes unnoticed
    indefinitely.
    """
    try:
        fire = trap.check(
            bars,
            current_idx=current_idx,
            layer_scores=layer_scores,
            proposed_direction=proposed_direction,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001 — a broken trap must not brick the stack
        log.debug("trap %r raised: %s", trap.trap_id, exc)
        _record_trap_result(trap.trap_id, ok=False)
        return None
    _record_trap_result(trap.trap_id, ok=True)
    return fire
