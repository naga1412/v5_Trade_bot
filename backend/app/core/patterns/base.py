"""Pattern detection primitives — spec §3.2.

`PatternFire` is the value object every pattern returns when detected.
`Pattern` is a Protocol describing the detector interface (no inheritance
required; any class with the matching attrs/method satisfies it).
"""
import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import pandas as pd

log = logging.getLogger(__name__)

Direction = Literal["LONG", "SHORT"]
PatternType = Literal["candle", "chart"]
_VALID_DIRECTIONS: frozenset[str] = frozenset({"LONG", "SHORT"})


@dataclass(frozen=True)
class PatternFire:
    """A single pattern detection at one bar.

    Attributes:
        pattern_id: stable string id (snake_case) used as the lookup key in
            `pattern_stats` and `pattern_enabled`.
        direction: "LONG" or "SHORT" — the bias the pattern implies.
        strength: how strongly the pattern is formed, in [0, 1].
        confidence: how clean / unambiguous the formation is, in [0, 1].
            Distinct from strength: a faint-but-clean pattern has low
            strength + high confidence; a strong-but-noisy pattern has the
            opposite.
        evidence: free-form dict for diagnostics (e.g. wick ratios, lookback,
            peak indices). Persisted into `predictions.layer_scores["2"].notes`
            up to a 500-char limit (see Phase E task E3).
    """
    pattern_id: str
    direction: Direction
    strength: float
    confidence: float
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        if self.direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}, "
                f"got {self.direction!r}"
            )
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0,1], got {self.strength}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")


@runtime_checkable
class Pattern(Protocol):
    """Detector protocol — every candle and chart pattern implements this."""
    pattern_id: str
    pattern_type: PatternType

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        """Run detection on `bars` ending at `current_idx`.

        Args:
            bars: DataFrame with columns `open`, `high`, `low`, `close`,
                `volume`, indexed by `pd.DatetimeIndex` ascending.
            current_idx: positional index (0-based) of the bar to evaluate.
                Patterns should look only at `bars.iloc[:current_idx + 1]`.

        Returns:
            `PatternFire` if the pattern is detected at `current_idx`,
            otherwise `None`. Must NOT raise on bad input — return `None`.
        """
        ...


# 2026-08-14 remediation work order B2: layer2_patterns.py and
# layer6_micro.py both wrapped `pat.detect(...)` in a bare
# `except Exception: continue` with zero logging — same failure class as
# the flow_features endpoint-swallow fixed in PR #423. A single pattern
# raising on one bar is expected (bad/short input, contractually should
# return None instead but a defensive catch is reasonable); the SAME
# pattern raising on every consecutive call is a structurally broken
# detector, exactly the shape that could leave any of the 17 traps (a
# sibling registry) dead without anyone noticing. Tracks failures per
# `pattern_id`, escalates once a specific pattern looks systematic.
_CONSECUTIVE_FAILURE_ALERT_THRESHOLD: int = 20
_consecutive_failures: dict[str, int] = {}


def _record_pattern_result(pattern_id: str, *, ok: bool) -> None:
    if ok:
        _consecutive_failures[pattern_id] = 0
        return
    streak = _consecutive_failures.get(pattern_id, 0) + 1
    _consecutive_failures[pattern_id] = streak
    if streak >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
        log.error(
            "pattern %r has raised on %d consecutive detect() calls — this "
            "looks like a structurally broken detector, not a one-off "
            "bad-bar edge case (Pattern.detect's contract is to return "
            "None on bad input, not raise). Same failure class as the "
            "flow_features endpoint-swallow fixed in PR #423.",
            pattern_id, streak,
        )


def _clear_pattern_failure_streaks_for_tests() -> None:
    _consecutive_failures.clear()


def detect_safe(
    pat: "Pattern", bars: pd.DataFrame, current_idx: int,
) -> "PatternFire | None":
    """Call `pat.detect()`, tracking per-pattern-id consecutive failures.

    A single broken pattern must not brick the whole layer (unchanged
    contract — this still returns None on any exception) but silently
    swallowing forever is how a genuinely broken detector goes unnoticed
    indefinitely. Shared by layer2_patterns.py and layer6_micro.py, the
    two consumers that iterate `ALL_PATTERNS`/a subset of it.
    """
    try:
        fire = pat.detect(bars, current_idx)
    except Exception as exc:  # noqa: BLE001 — a broken pattern must not brick the layer
        log.debug("pattern %r raised: %s", pat.pattern_id, exc)
        _record_pattern_result(pat.pattern_id, ok=False)
        return None
    _record_pattern_result(pat.pattern_id, ok=True)
    return fire
