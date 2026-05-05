"""Trap detection primitives — spec §3.3.

A `TrapFire` is the value object every trap returns when it fires. A `Trap`
is a Protocol describing the detector interface — mirrors `Pattern`/`PatternFire`
in `app/core/patterns/base.py`. `TrapContext` carries cross-cutting inputs
(news calendar, weekly bias, BTC volatility, funding rate) that traps share.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import pandas as pd

from app.core.scoring.types import Direction, LayerScore

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
