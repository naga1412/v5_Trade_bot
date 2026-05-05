"""Trap registry — populated by importing each trap module below.

Phases C and D append concrete trap instances. The orchestrator
`app/core/scoring/run_traps.py:check_all_traps()` iterates this list and
filters by `enabled_set` (per-symbol/per-TF admin disables) at run time.
"""
from app.core.scoring.traps.base import Trap, TrapContext, TrapFire  # noqa: F401

ALL_TRAPS: list[Trap] = []
"""Filled by Phase C (12 main traps) + Phase D (5 short-only)."""
