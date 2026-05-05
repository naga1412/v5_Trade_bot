"""pre_news_event trap — high-impact news within 60 minutes (extreme, both sides)."""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_NEWS_WINDOW_MIN: int = 60


class PreNewsEventTrap:
    """Fire when a high-impact news event is imminent (< 60 minutes away)."""

    trap_id: str = "pre_news_event"
    severity: Severity = "extreme"
    side: Side = "both"

    def check(
        self,
        bars: pd.DataFrame,
        *,
        current_idx: int,
        layer_scores: dict[int, LayerScore | None],
        proposed_direction: Direction,
        context: TrapContext,
    ) -> TrapFire | None:
        minutes = context.next_news_event_minutes_until
        if minutes is None or minutes >= _NEWS_WINDOW_MIN or minutes < 0:
            return None
        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=f"high-impact news in {minutes}min (< {_NEWS_WINDOW_MIN}min window)",
            evidence={"minutes_until": minutes, "window_min": _NEWS_WINDOW_MIN},
        )
