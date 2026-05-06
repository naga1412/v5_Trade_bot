"""Champion/challenger evaluation gate.

Implementation lands in Phase F (spec §6.5). This stub exists so the
admin promotion route + Phase B backtest tests can import the symbol
without ImportError on the *module*.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def evaluate_challenger(  # pragma: no cover — stub
    session: "AsyncSession",
    *,
    challenger_version: str,
    champion_version: str,
) -> dict[str, Any]:
    """Run the champion/challenger backtest gate. Phase F deliverable.

    TODO(SP-7 Phase F): pull both checkpoints, replay the last N days of
    OHLCV through tools.backtest.run_backtest for each, and return the
    head-to-head metrics dict the admin promotion route consumes.
    """
    raise NotImplementedError("evaluate_challenger: Phase F deliverable")
