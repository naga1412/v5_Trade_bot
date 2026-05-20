"""PR-strategy-1: entry-quality gate.

Two flag-gated checks, both default OFF:

  1. `DISABLE_SHORT_SIGNALS`: when True, deny every SHORT signal.
  2. `MIN_ENTRY_SCORE_LONG`: when not None, deny every LONG signal whose
     `entry_score` is below the threshold. None entry_score (e.g. the
     manual-test-trade path that constructs `SignalProposal` without
     wiring a pred) is treated as "no score available" and allowed —
     the manual entry is the operator's deliberate choice.

The function is duck-typed: `signal` only needs `.direction` (str
"LONG"/"SHORT") and `.entry_score` (float | None). Both call sites
satisfy this — `ShadowSignal` has `.direction` (Direction enum) and
exposes the score via `.score`; the shadow worker uses a thin shim that
maps `.score → .entry_score` so this module stays uniform. The
dispatcher passes a `SignalProposal` which has `.entry_score` directly
(threaded from `pred.final.score` via `proposal_from_prediction`).

`Direction` is compared by `.value` (or coerced via `str()`) so passing
a plain string or the enum both work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AllowDecision:
    """Result of a gate check. `reason` is a short stable ID for metrics
    + structured logs; `None` when allowed."""

    allow: bool
    reason: str | None = None


def _direction_str(value: Any) -> str:
    """Coerce a direction-shaped value (Direction enum or str) to upper-cased
    string. Defensive against unusual inputs (returns "" → never matches)."""
    if value is None:
        return ""
    # Enum-like: prefer .value when present
    inner = getattr(value, "value", value)
    try:
        return str(inner).upper()
    except Exception:  # noqa: BLE001 — defensive fallback
        return ""


def open_position_gate(signal: Any, settings: Any) -> AllowDecision:
    """Decide whether the entry should be allowed through.

    Logic (order is significant — SHORT-disable runs first so an operator
    disabling SHORTs sees `short_disabled` rather than a LONG-threshold
    code when both flags happen to be active):

      1. direction == "SHORT" + DISABLE_SHORT_SIGNALS → deny("short_disabled").
      2. direction == "LONG" + MIN_ENTRY_SCORE_LONG is not None
         + entry_score is not None
         + entry_score < threshold → deny("below_long_threshold").
      3. Otherwise → allow.

    The `entry_score is None` short-circuit on step 2 is deliberate:
    code paths that build a SignalProposal without wiring a `pred`
    (admin_test_trade, ad-hoc operator-driven manual entry) should not
    be blocked when the operator hasn't opted into the threshold via env.
    """
    direction = _direction_str(getattr(signal, "direction", None))

    if direction == "SHORT" and getattr(settings, "DISABLE_SHORT_SIGNALS", False):
        return AllowDecision(allow=False, reason="short_disabled")

    if direction == "LONG":
        threshold = getattr(settings, "MIN_ENTRY_SCORE_LONG", None)
        entry_score = getattr(signal, "entry_score", None)
        if threshold is not None and entry_score is not None:
            if float(entry_score) < float(threshold):
                return AllowDecision(allow=False, reason="below_long_threshold")

    return AllowDecision(allow=True, reason=None)
