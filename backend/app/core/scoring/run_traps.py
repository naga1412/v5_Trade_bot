"""SP-5 Phase E4 — trap orchestrator.

Iterates ``ALL_TRAPS`` at one bar, filters by ``enabled_set`` (per-symbol /
per-TF admin disables), wraps each ``.check()`` in try/except so a single
broken detector cannot brick the whole stack. Returns the list of fires
(possibly empty), in registry order.

The ``enabled_set`` semantics:
  - ``None`` -> every registered trap runs (default behaviour).
  - ``set[str]`` -> only traps whose ``trap_id`` is in the set may fire;
    others are skipped without invoking ``check()``.
  - ``set()`` (empty) -> no trap runs; returns ``[]``.

The admin-tunable enable/disable lookup is owned by
``app.core.scoring._trap_enabled_cache`` (Phase F wires the cache into
``predictor.build_prediction``). This module only consumes the resolved set.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps import ALL_TRAPS
from app.core.scoring.traps.base import TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore


def check_all_traps(
    *,
    bars: pd.DataFrame,
    current_idx: int,
    layer_scores: dict[int, LayerScore | None],
    proposed_direction: Direction,
    context: TrapContext,
    enabled_set: set[str] | None = None,
) -> list[TrapFire]:
    """Run every enabled trap at ``current_idx`` and collect their fires.

    A single trap raising an exception is silently swallowed (logged only by
    upstream observability if wired) — by contract a broken detector must not
    block the rest. Per ``Trap.check`` docstring, well-behaved traps return
    ``None`` instead of raising on bad input.
    """
    fires: list[TrapFire] = []
    for trap in ALL_TRAPS:
        if enabled_set is not None and trap.trap_id not in enabled_set:
            continue
        try:
            fire = trap.check(
                bars,
                current_idx=current_idx,
                layer_scores=layer_scores,
                proposed_direction=proposed_direction,
                context=context,
            )
        except Exception:  # noqa: BLE001 — a broken trap must not brick the stack
            continue
        if fire is not None:
            fires.append(fire)
    return fires
