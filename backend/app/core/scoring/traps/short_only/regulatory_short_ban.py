"""regulatory_short_ban trap — PLACEHOLDER (medium, short).

v1 of SP-5 has no live regulatory feed, so this trap always returns `None`.
The module exists so the Phase E orchestrator can iterate over `ALL_TRAPS`
with all 17 entries; SP-9 (news + filings ingest) wires the real signal.

When implemented, this trap will fire when the underlying instrument is
under an active short-sale restriction (regulatory ban, exchange-imposed
SSR, sanctions list) — shorting is then operationally blocked or
prohibitively risky regardless of the chart.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore


class RegulatoryShortBanTrap:
    """Placeholder — always returns None until SP-9 wires the regulatory feed."""

    trap_id: str = "regulatory_short_ban"
    severity: Severity = "medium"
    side: Side = "short"

    def check(
        self,
        bars: pd.DataFrame,
        *,
        current_idx: int,
        layer_scores: dict[int, LayerScore | None],
        proposed_direction: Direction,
        context: TrapContext,
    ) -> TrapFire | None:
        # No live regulatory feed in SP-5 v1 — placeholder for SP-9.
        return None
