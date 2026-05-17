"""FU-1: every worker module wires record_heartbeat() with its registry name.

PR #97 declared 8 workers with the HEARTBEAT liveness query but didn't ship
the in-loop ``record_heartbeat(...)`` calls — they sat as
``pending_heartbeat=True`` placeholders. FU-1 closes that gap by wiring
the call into every worker's main loop. This test is the static gate that
catches a future worker that's added to the registry but forgets the
heartbeat call (the watchdog would silently report ``never_heartbeated``
forever).

For each worker source module we assert two things:

  1. The source imports / calls ``record_heartbeat``.
  2. The source contains the worker's registry ``name`` as a literal
     string (so the heartbeat row uses the registry name, not a typo'd
     variant like ``"live_workers"`` that the watchdog can't correlate).

This complements ``test_worker_registry_consistency`` which asserts
either log.info() OR record_heartbeat() exists (PR #97's looser bar).
After FU-1 the bar is "record_heartbeat AND the correct name".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ops.worker_registry import WORKER_REGISTRY
from tests.unit.test_worker_registry_consistency import WORKER_SOURCE_MODULES


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "worker_name",
    [s.name for s in WORKER_REGISTRY],
)
def test_worker_source_contains_record_heartbeat_call(worker_name: str) -> None:
    """Worker source must contain a literal ``record_heartbeat(`` call."""
    rel = WORKER_SOURCE_MODULES[worker_name]
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "record_heartbeat(" in src, (
        f"{worker_name} ({rel}) has no record_heartbeat(...) call. "
        f"FU-1 requires every worker to heartbeat from its main loop so "
        f"the watchdog can observe liveness even when the natural table "
        f"query returns no rows."
    )


@pytest.mark.parametrize(
    "worker_name",
    [s.name for s in WORKER_REGISTRY],
)
def test_worker_source_references_its_registry_name(worker_name: str) -> None:
    """Worker source must contain the literal registry name as a string.

    Catches the case where a worker was wired with record_heartbeat() but
    used a typo'd name (e.g. ``"live_workers"`` vs registry ``"live_worker"``)
    — the watchdog couldn't correlate the heartbeat rows back to the spec
    and would still report ``never_heartbeated``.

    The check is intentionally permissive: any occurrence of the literal
    string is enough (constants, function args, comments — all count). A
    refactor that hoists the name into a ``WORKER_NAME`` constant
    naturally keeps the literal present.
    """
    rel = WORKER_SOURCE_MODULES[worker_name]
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert worker_name in src, (
        f"{worker_name}: registry name not present as a literal string "
        f"in {rel}. The heartbeat row would land under a different "
        f"worker_name and the watchdog couldn't match it to the spec."
    )
