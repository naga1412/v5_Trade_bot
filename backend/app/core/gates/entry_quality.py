"""PR-strategy-1 + PR-BOT-INTELLIGENCE-UPGRADE: entry-quality gate.

The shipped PR-strategy-1 gate emits two reasons:

  1. ``DISABLE_SHORT_SIGNALS``: when True, deny every SHORT signal.
  2. ``MIN_ENTRY_SCORE_LONG``: when not None, deny every LONG whose
     ``entry_score`` is below the threshold. ``None`` entry_score (e.g.
     the manual-test-trade path) is treated as "no score available" and
     allowed.

PR-BOT-INTELLIGENCE-UPGRADE adds three more sub-gates. Each is independently
flag-gated and defaults OFF — when all new flags are off, the behaviour is
byte-identical to PR-strategy-1.

  3. ``REGIME_GATE_ENABLED``: deny LONG in confirmed ``bear`` regime and
     SHORT in confirmed ``bull`` regime, **unless** Layer 2 emits a strong
     contrarian pattern (`layer2_direction` matches signal direction AND
     `layer2_confidence >= REGIME_OPPOSITE_PATTERN_OVERRIDE`).
  4. ``PATTERN_BOOST_ENABLED``: when Layer 2 agrees with the signal at
     `layer2_confidence >= PATTERN_BOOST_MIN_CONFIDENCE`, the effective
     score used for `MIN_ENTRY_SCORE_LONG` is `entry_score + PATTERN_BOOST_AMOUNT`.
     When Layer 2 opposes at the same confidence floor, the effective
     score is `entry_score - PATTERN_PENALTY_AMOUNT`.
  5. ``ADX_GATE_ENABLED``: deny when the MTF dominant-TF ADX(14) is below
     `MIN_ADX_TREND_STRENGTH`. Fail-open: if `mtf_adx_by_tf` / `dominant_tf`
     / lookup is missing, the gate passes.

The function is duck-typed: ``signal`` only needs ``.direction`` and
``.entry_score`` plus, optionally, ``.layer2_direction`` /
``.layer2_confidence`` / ``.mtf_adx_by_tf`` / ``.mtf_dominant_tf``. ``Direction``
is compared by ``.value`` (or coerced via ``str()``) so passing a plain string
or the enum both work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AllowDecision:
    """Result of a gate check. ``reason`` is a short stable ID for metrics
    + structured logs; ``None`` when allowed."""

    allow: bool
    reason: str | None = None


@dataclass(frozen=True)
class GateVerdict:
    """One sub-gate's individual outcome, for the dispatch decision log
    (item 3, 2026-08-14) — never used to make the actual dispatch
    decision. ``verdict`` is one of "pass" / "block" / "not_evaluated"
    (flag off, or this check doesn't apply to the signal's direction)."""

    gate: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class _CheckResult:
    """Internal: one sub-check's outcome, shared by open_position_gate's
    short-circuit chain and evaluate_all_gates' unconditional sweep.
    ``legacy_reason`` is the exact string open_position_gate has always
    returned in AllowDecision.reason — kept byte-identical on purpose so
    this refactor changes no observable behavior. ``verbose_reason`` is
    a richer, always-present description for the decision log."""

    evaluated: bool
    blocked: bool
    legacy_reason: str | None
    verbose_reason: str


def _direction_str(value: Any) -> str:
    """Coerce a direction-shaped value (Direction enum or str) to upper-cased
    string. Defensive against unusual inputs (returns "" → never matches)."""
    if value is None:
        return ""
    inner = getattr(value, "value", value)
    try:
        return str(inner).upper()
    except Exception:  # noqa: BLE001 — defensive fallback
        return ""


def _layer2_agrees_with_signal(
    signal_direction: str,
    signal: Any,
    min_confidence: float,
) -> bool:
    """True when Layer 2 emits a same-direction pattern at >= ``min_confidence``."""
    l2_dir = _direction_str(getattr(signal, "layer2_direction", None))
    l2_conf_raw = getattr(signal, "layer2_confidence", None)
    if l2_dir == "" or l2_conf_raw is None:
        return False
    try:
        l2_conf = float(l2_conf_raw)
    except (TypeError, ValueError):
        return False
    return l2_dir == signal_direction and l2_conf >= min_confidence


def _layer2_opposes_signal(
    signal_direction: str,
    signal: Any,
    min_confidence: float,
) -> bool:
    """True when Layer 2 emits an opposite-direction pattern at >= ``min_confidence``.

    NEUTRAL layer-2 votes do NOT count as opposing (neutral is "no opinion").
    """
    l2_dir = _direction_str(getattr(signal, "layer2_direction", None))
    l2_conf_raw = getattr(signal, "layer2_confidence", None)
    if l2_dir in ("", "NEUTRAL") or l2_conf_raw is None:
        return False
    try:
        l2_conf = float(l2_conf_raw)
    except (TypeError, ValueError):
        return False
    return l2_dir != signal_direction and l2_conf >= min_confidence


def _dominant_tf_adx(signal: Any) -> float | None:
    """Look up ADX of ``signal.mtf_dominant_tf`` in ``signal.mtf_adx_by_tf``.

    Returns ``None`` when any of (mtf_adx_by_tf, mtf_dominant_tf, the per-TF
    entry, the value's numeric coerce) is missing — gate falls open.
    """
    adx_map = getattr(signal, "mtf_adx_by_tf", None)
    dominant = getattr(signal, "mtf_dominant_tf", None)
    if not isinstance(adx_map, dict) or not isinstance(dominant, str):
        return None
    raw = adx_map.get(dominant)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _check_short_disabled(direction: str, settings: Any) -> _CheckResult:
    if direction == "SHORT" and getattr(settings, "DISABLE_SHORT_SIGNALS", False):
        return _CheckResult(True, True, "short_disabled", "DISABLE_SHORT_SIGNALS=true")
    return _CheckResult(True, False, None, "short signals not disabled, or direction != SHORT")


def _check_regime(
    direction: str, signal: Any, settings: Any, market_regime: str | None,
) -> _CheckResult:
    if not getattr(settings, "REGIME_GATE_ENABLED", False):
        return _CheckResult(False, False, None, "REGIME_GATE_ENABLED=false")
    if market_regime not in ("bull", "bear"):
        return _CheckResult(
            True, False, None, f"market_regime={market_regime!r}, no opposing case",
        )
    opposing = (
        (direction == "LONG" and market_regime == "bear")
        or (direction == "SHORT" and market_regime == "bull")
    )
    if not opposing:
        return _CheckResult(
            True, False, None,
            f"regime={market_regime} does not oppose direction={direction}",
        )
    override_floor = float(getattr(settings, "REGIME_OPPOSITE_PATTERN_OVERRIDE", 0.8))
    if _layer2_agrees_with_signal(direction, signal, override_floor):
        return _CheckResult(
            True, False, None,
            f"opposing regime={market_regime} overridden by L2 agreement>={override_floor:.2f}",
        )
    reason = f"blocked_wrong_regime: regime={market_regime}, direction={direction}"
    return _CheckResult(True, True, reason, reason)


def _check_adx(signal: Any, settings: Any) -> _CheckResult:
    if not getattr(settings, "ADX_GATE_ENABLED", False):
        return _CheckResult(False, False, None, "ADX_GATE_ENABLED=false")
    adx = _dominant_tf_adx(signal)
    if adx is None:
        return _CheckResult(
            True, False, None, "mtf_adx_by_tf/dominant_tf missing -- fail-open",
        )
    threshold = float(getattr(settings, "MIN_ADX_TREND_STRENGTH", 25.0))
    dominant = getattr(signal, "mtf_dominant_tf", None)
    if adx < threshold:
        reason = (
            f"blocked_low_trend_strength: adx={adx:.1f} < "
            f"{threshold:.1f} (dominant_tf={dominant})"
        )
        return _CheckResult(True, True, reason, reason)
    return _CheckResult(
        True, False, None,
        f"adx={adx:.1f} >= {threshold:.1f} (dominant_tf={dominant})",
    )


def _check_min_score(direction: str, signal: Any, settings: Any) -> _CheckResult:
    if direction != "LONG":
        return _CheckResult(False, False, None, "direction != LONG")
    long_threshold = getattr(settings, "MIN_ENTRY_SCORE_LONG", None)
    entry_score = getattr(signal, "entry_score", None)
    if long_threshold is None or entry_score is None:
        return _CheckResult(
            True, False, None, "threshold or entry_score missing -- fail-open",
        )
    effective_score = float(entry_score)
    boost_detail: str | None = None
    if getattr(settings, "PATTERN_BOOST_ENABLED", False):
        min_conf = float(getattr(settings, "PATTERN_BOOST_MIN_CONFIDENCE", 0.6))
        boost = float(getattr(settings, "PATTERN_BOOST_AMOUNT", 0.10))
        penalty = float(getattr(settings, "PATTERN_PENALTY_AMOUNT", 0.15))
        if _layer2_agrees_with_signal(direction, signal, min_conf):
            effective_score = min(1.0, effective_score + boost)
            boost_detail = (
                f"effective_score={effective_score:.3f}, "
                f"base={float(entry_score):.3f} + L2_boost={boost:.2f}, "
                f"L2 conf={float(getattr(signal, 'layer2_confidence', 0.0)):.2f}"
            )
        elif _layer2_opposes_signal(direction, signal, min_conf):
            effective_score = max(-1.0, effective_score - penalty)
            boost_detail = (
                f"effective_score={effective_score:.3f}, "
                f"base={float(entry_score):.3f} - L2_penalty={penalty:.2f}, "
                f"L2 conf={float(getattr(signal, 'layer2_confidence', 0.0)):.2f}"
            )
    if effective_score < float(long_threshold):
        legacy = (
            f"below_long_threshold ({boost_detail})"
            if boost_detail is not None else "below_long_threshold"
        )
        verbose = f"effective_score={effective_score:.3f} < {float(long_threshold):.3f}"
        if boost_detail is not None:
            verbose += f" ({boost_detail})"
        return _CheckResult(True, True, legacy, verbose)
    verbose = f"effective_score={effective_score:.3f} >= {float(long_threshold):.3f}"
    return _CheckResult(True, False, None, verbose)


def open_position_gate(
    signal: Any,
    settings: Any,
    *,
    market_regime: str | None = None,
) -> AllowDecision:
    """Decide whether the entry should be allowed through.

    Logic order (significant — earlier checks win so an operator disabling
    SHORTs sees ``short_disabled`` rather than a regime/ADX code):

      1. direction == "SHORT" + DISABLE_SHORT_SIGNALS → deny("short_disabled").
      2. REGIME_GATE_ENABLED + ``market_regime`` opposes direction → deny
         ("blocked_wrong_regime: regime=<r>, direction=<d>"), UNLESS Layer 2
         emits a same-direction pattern at confidence >=
         REGIME_OPPOSITE_PATTERN_OVERRIDE.
      3. ADX_GATE_ENABLED + dominant-TF ADX < MIN_ADX_TREND_STRENGTH →
         deny ("blocked_low_trend_strength: adx=X.X < Y.Y (dominant_tf=Z)").
      4. direction == "LONG": apply optional Layer-2 boost/penalty to the
         entry score, then compare to MIN_ENTRY_SCORE_LONG. ``None``
         entry_score short-circuits to allow (manual operator path).
      5. Otherwise → allow.

    ``market_regime`` is a Literal ``"bull"|"bear"|"neutral"`` (or None when
    REGIME_GATE_ENABLED is off / the fetch failed). The dispatcher fetches
    this via ``app.core.regime.get_cached_market_regime()`` and passes it in.
    Keeping it as a kwarg (not a signal attribute) lets the test suite stub
    regimes without constructing a SignalProposal with that field.

    2026-08-14: each sub-check's boolean/threshold logic now lives in a
    shared ``_check_*`` helper also used by :func:`evaluate_all_gates`
    (the dispatch decision log's unconditional, log-only sweep) — single
    source of truth, so the log can never disagree with what actually
    happened. This function's control flow and every returned reason
    string are unchanged from before the refactor.
    """
    direction = _direction_str(getattr(signal, "direction", None))

    r = _check_short_disabled(direction, settings)
    if r.blocked:
        return AllowDecision(allow=False, reason=r.legacy_reason)

    r = _check_regime(direction, signal, settings, market_regime)
    if r.blocked:
        return AllowDecision(allow=False, reason=r.legacy_reason)

    r = _check_adx(signal, settings)
    if r.blocked:
        return AllowDecision(allow=False, reason=r.legacy_reason)

    r = _check_min_score(direction, signal, settings)
    if r.blocked:
        return AllowDecision(allow=False, reason=r.legacy_reason)

    return AllowDecision(allow=True, reason=None)


def evaluate_all_gates(
    signal: Any,
    settings: Any,
    *,
    market_regime: str | None = None,
) -> list[GateVerdict]:
    """Log-only: every sub-gate evaluated UNCONDITIONALLY, via the exact
    same ``_check_*`` helpers :func:`open_position_gate` short-circuits
    through — never used to make the actual dispatch decision, and
    incapable of disagreeing with it about whether a given sub-check
    would block, since both call the same functions.

    This exists so the dispatch decision log (item 3, 2026-08-14) doesn't
    reproduce open_position_gate's blind spot: a naive log of just the
    final AllowDecision would still only show the FIRST blocking reason,
    which is exactly what forced a 65-second kline-replay simulation to
    answer "what would ADX have said" during the 2026-08 ADX-gate
    argument. All 4 sub-checks are zero-I/O (market_regime is fetched
    once by the caller and passed in), so evaluating every one costs
    nothing dispatch-path-relevant.
    """
    direction = _direction_str(getattr(signal, "direction", None))

    def _verdict(gate: str, r: _CheckResult) -> GateVerdict:
        if not r.evaluated:
            return GateVerdict(gate, "not_evaluated", r.verbose_reason)
        return GateVerdict(gate, "block" if r.blocked else "pass", r.verbose_reason)

    return [
        _verdict("short_disabled", _check_short_disabled(direction, settings)),
        _verdict("regime", _check_regime(direction, signal, settings, market_regime)),
        _verdict("adx", _check_adx(signal, settings)),
        _verdict("min_score", _check_min_score(direction, signal, settings)),
    ]
