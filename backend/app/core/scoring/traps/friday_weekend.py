"""friday_weekend trap — Friday late or weekend bar (high, both sides).

Fires when the current bar's UTC timestamp falls in:
  - Friday after 20:00 UTC, or
  - Saturday (full day), or
  - Sunday (full day)

Captures weekend gap risk: positions held over the weekend without a liquid
close are vulnerable to overnight news.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_FRIDAY_HOUR_CUTOFF: int = 20
# Mon=0 ... Fri=4, Sat=5, Sun=6
_FRIDAY: int = 4
_SATURDAY: int = 5
_SUNDAY: int = 6


class FridayWeekendTrap:
    """Fire on Friday >= 20:00 UTC, all of Saturday, or all of Sunday."""

    trap_id: str = "friday_weekend"
    severity: Severity = "high"
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
        if current_idx < 0 or current_idx >= len(bars):
            return None
        ts = bars.index[current_idx]
        if not isinstance(ts, pd.Timestamp):
            return None
        # Convert to UTC if tz-aware; else treat as UTC
        if ts.tzinfo is not None:
            ts_utc = ts.tz_convert("UTC")
        else:
            ts_utc = ts.tz_localize("UTC")

        weekday = int(ts_utc.weekday())
        hour = int(ts_utc.hour)

        is_weekend = weekday in (_SATURDAY, _SUNDAY)
        is_friday_late = weekday == _FRIDAY and hour >= _FRIDAY_HOUR_CUTOFF
        if not (is_weekend or is_friday_late):
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"weekend gap risk — bar at {ts_utc.isoformat()} "
                f"(weekday={weekday}, hour={hour})"
            ),
            evidence={
                "weekday": weekday,
                "hour": hour,
                "is_weekend": is_weekend,
                "is_friday_late": is_friday_late,
            },
        )
